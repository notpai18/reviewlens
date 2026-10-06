# ReviewLens Failure Log & Error Categorization

This document records systematic failures encountered during ReviewLens evaluation and operational testing, along with root-cause investigations, failure taxonomy, before/after traces, and architectural remediations.

---

## 1. Failure Category: Small-Sample Ranking Bias (`small_sample_ranking_bias`)

### 1.1 Overview & Severity
- **Failure Type**: Semantic SQL generation bias / Unconstrained aggregation ranking / Small-sample noise
- **Impact**: High. When users ask natural-language ranking questions involving averages, ratios, or percentages without explicitly specifying sample size thresholds, unconstrained queries surface single-review or low-volume outliers rather than statistically significant trends.
- **Affected Queries**:
  - *"What is the lowest rated version?"*
  - *"highest rated version"*
  - *"which month has the best rating"*
  - Cross-game ranking queries without game filtering or grouping.

---

### 1.2 Original Failure Cases (Before Remediation)

#### Case 1: "What is the lowest rated version?"
- **User Question**: `"What is the lowest rated version?"`
- **Initial Plan**:
  ```json
  {
    "intent": "analytics",
    "standalone_question": "What is the lowest rated version?",
    "tools": ["sql"],
    "reason": "Calculate lowest average rating grouped by app version"
  }
  ```
- **Generated SQL (Unconstrained)**:
  ```sql
  SELECT app_version, ROUND(AVG(rating), 2) AS avg_rating
  FROM reviews
  WHERE app_version IS NOT NULL
  GROUP BY app_version
  ORDER BY avg_rating ASC
  LIMIT 1
  ```
- **Execution Output**:
  - `app_version`: `2.4.102` (or `5.6.202`)
  - `avg_rating`: `1.0`
  - Underlying count: **1 review**
- **Original Synthesized Answer**:
  - *Summary*: "The lowest rated version is 2.4.102 with an average rating of 1.0 stars."
  - *Findings*: `[{"statement": "Version 2.4.102 has an average rating of 1.0 stars.", "evidence_ids": ["SQL#1"], "kind": "observed"}]`
  - *Caveats*: None or volunteered comments like "No review text was retrieved explaining why players gave 1-star ratings to this version."
- **Flaws Identified**:
  1. **Noise Domination**: Version `2.4.102` has only 1 single review in the database. The actual lowest-rated production release with statistical significance is Marvel Snap `41.11.1` (609 reviews, average rating 1.31) or `34.11.4` (1,137 reviews, average rating 1.33).
  2. **Cross-Game Version Collision**: `app_version` strings (e.g., `2.4.102`, `1.0.0`) belong to specific games. Omitting `game` from `SELECT` and `GROUP BY` loses game attribution and collides identical version strings across different titles.
  3. **Feedback Hallucination in Synthesis**: The synthesizer stated that qualitative review feedback was missing even though the user asked a purely quantitative ranking question.

---

#### Case 2: "highest rated version"
- **User Question**: `"highest rated version"`
- **Initial Plan**:
  ```json
  {
    "intent": "analytics",
    "standalone_question": "highest rated version",
    "tools": ["sql"],
    "reason": "Rank app versions by average rating descending"
  }
  ```
- **Generated SQL (Unconstrained)**:
  ```sql
  SELECT app_version, ROUND(AVG(rating), 2) AS avg_rating
  FROM reviews
  WHERE app_version IS NOT NULL
  GROUP BY app_version
  ORDER BY avg_rating DESC
  LIMIT 1
  ```
- **Execution Output**:
  - `app_version`: `3.7.202`
  - `avg_rating`: `5.0`
  - Underlying count: **1 review**
- **Flaws Identified**:
  - Single 5-star review artificially ranked as the "highest rated version", hiding actual high-rated major releases such as Cookie Run: Kingdom `7.8.105` (56 reviews, average rating 4.89) and `7.8.102` (385 reviews, average rating 4.73).

---

#### Case 3: "which month has the best rating"
- **User Question**: `"which month has the best rating"`
- **Generated SQL (Unconstrained)**:
  ```sql
  SELECT strftime(review_date, '%Y-%m') AS month, ROUND(AVG(rating), 2) AS avg_rating
  FROM reviews
  GROUP BY month
  ORDER BY avg_rating DESC
  LIMIT 1
  ```
- **Flaws Identified**:
  - Omits `HAVING COUNT(*) >= 30` and omits review volume count in the projection, risking sparse months or boundary windows dominating the ranking.

---

### 1.3 Root Cause Analysis

1. **Prompt Under-Specification**:
   `sql_generate.md` previously instructed models to add `ORDER BY` for rankings, but did not enforce a minimum sample size (`HAVING COUNT(*) >= 30`) or the one-game-per-version rule (`GROUP BY game, app_version`). LLMs defaulted to standard textbook aggregation queries lacking sample size thresholds.
2. **Missing AST Validation Guard**:
   The SQL validator checked syntax, read-only constraints, and limit enforcement, but had no semantic AST guard to intercept queries with `GROUP BY`, `ORDER BY` on an average/ratio, and no `HAVING` clause.
3. **Missing Verification Caveat**:
   `verify_node` lacked a deterministic check on returned SQL rows to warn users when sample sizes were underpowered (< 30 reviews).
4. **Synthesis Prompt Over-Explanation**:
   The synthesis prompt previously did not restrict comments about missing qualitative reviews, leading the model to volunteer negative claims ("no player feedback was retrieved") for pure ranking and SQL queries.

---

### 1.4 Remediations Implemented

#### 1. SQL Prompt Rules (`src/reviewlens/prompts/sql_generate.md`)
Added explicit hard rules:
- **One game per version**: Version numbers belong to a specific game. When grouping by version (`app_version`), always include `game` in `SELECT` and `GROUP BY` (e.g. `GROUP BY game, app_version` or filter by game if the question specifies one). Never group by `app_version` alone across multiple games.
- **Minimum sample size for rankings**: When ranking by an average, ratio, or percentage (`ORDER BY AVG/ratio`), always require a minimum sample size with `HAVING COUNT(*) >= 30` (or the threshold specified in the question), and include `COUNT(*)` in the `SELECT` list so sample sizes are clear.

#### 2. AST Guard & Repair Routing (`src/reviewlens/sql/validator.py` & `generate.py`)
- Implemented `is_small_sample_ranking(sql: str) -> bool`:
  Parses the AST via `sqlglot`. If a `SELECT` statement has `GROUP BY` and orders by an `AVG`, division, ratio expression, or ratio alias, but has no `HAVING` clause, the guard triggers.
  - Specifically allows queries where `ORDER BY` is `COUNT(*)` or count-based aliases (e.g. "Which game has the most reviews?").
- In `SQLGenerator.run`: When `is_small_sample_ranking` detects an unconstrained query and attempts remain, the query is marked with status `"small_sample_guard"` and routed to `_call_repair` with actionable feedback:
  `"Small-sample ranking guard: Query has GROUP BY and ORDER BY on an average or ratio without a HAVING clause. Add HAVING COUNT(*) >= 30 (or appropriate minimum sample size) to prevent small-sample bias."`

#### 3. Deterministic Caveat in Verification (`src/reviewlens/agent/nodes.py`)
- Implemented `has_sql_row_below_threshold(sql_res, threshold=30) -> bool`:
  Scans all count columns in SQL result rows. If any row has a sample count < 30, appends the deterministic caveat:
  `"Some reported results are based on fewer than 30 reviews."`

#### 4. Synthesis Prompt Guard (`src/reviewlens/prompts/synthesize.md`)
- Added Rule 11:
  `"Only mention missing player reviews or lack of feedback if the QUESTION explicitly asked for player opinions, feedback, or qualitative reasons. When answering numerical or ranking questions (e.g., lowest/highest rated version, best rating month, counts, averages), answer directly from the data and do NOT mention that review feedback or qualitative explanations are missing."`

---

### 1.5 Verification & Unit Testing

- `tests/unit/test_sql_validator.py`: Tested `is_small_sample_ranking` with positive triggers (AVG, ratio, division, column aliases) and negative controls (`HAVING COUNT(*) >= 30`, `ORDER BY COUNT(*)`, unaggregated ORDER BY).
- `tests/unit/test_sql_generate.py`: Verified guard triggers `_call_repair` in `SQLGenerator.run` and allows `ORDER BY COUNT(*)` without repair.
- `tests/unit/test_citations_split.py`: Verified `SMALL_SAMPLE_SQL_CAVEAT` is added when count < 30 and omitted when counts >= 30 or absent.
- Full unit test suite passes: 296/296 tests passing.

---

### 1.6 Before vs After Empirical Results

| Question | Metric / Aspect | BEFORE Remediation | AFTER Remediation |
|---|---|---|---|
| **"What is the lowest rated version?"** | **SQL Query** | `SELECT game, app_version, ROUND(AVG(rating), 2) AS avg_rating, COUNT(*) AS review_count FROM reviews WHERE NOT app_version IS NULL GROUP BY game, app_version ORDER BY avg_rating ASC LIMIT 500` (No `HAVING`) | `SELECT game, app_version, ROUND(AVG(rating), 2) AS avg_rating, COUNT(*) AS review_count FROM reviews WHERE NOT app_version IS NULL GROUP BY game, app_version HAVING COUNT(*) >= 30 ORDER BY avg_rating ASC LIMIT 1` |
| | **Top Result** | Marvel Snap `15.17.0` (avg 1.00, **3 reviews**) and CRK `2.0.302` (avg 1.00, **1 review**) tied at 1.0 | Marvel Snap `41.11.1` (avg 1.31, **609 reviews**) |
| | **Synthesized Answer** | "Multiple versions across games share the lowest possible average rating of 1.0. These include versions from both Marvel Snap and Cookie Run: Kingdom. Findings: Marvel Snap version 15.17.0 is 1.0 based on 3 reviews; Cookie Run: Kingdom version 2.0.302 is 1.0 based on 1 review." | "The lowest rated version across all games is version 41.11.1 of Marvel Snap with an average rating of 1.31 based on 609 reviews." |
| **"highest rated version"** | **SQL Query** | Windowed `ROW_NUMBER() OVER (PARTITION BY game ORDER BY avg_rating DESC)` without sample filter | `SELECT game, app_version, ROUND(AVG(rating), 2) AS avg_rating, COUNT(*) AS review_count FROM reviews WHERE NOT app_version IS NULL GROUP BY game, app_version HAVING COUNT(*) >= 30 ORDER BY avg_rating DESC LIMIT 500` |
| | **Top Result** | Cookie Run: Kingdom `7.0.202` (avg 5.00, **7 reviews**); Marvel Snap `3.0.5` (avg 4.00, **1 review**) | Cookie Run: Kingdom `7.8.105` (avg 4.89, **56 reviews**); Marvel Snap `53.15.12` (avg 3.85, **202 reviews**) |
| | **Synthesized Answer** | Cited version 7.0.202 (7 reviews) and version 3.0.5 (1 review). | Clean breakdown per game with robust sample sizes: CRK 7.8.105 (4.89, 56 reviews) and Marvel Snap 53.15.12 (3.85, 202 reviews). |
| **"which month has the best rating"** | **SQL Query** | `SELECT DATE_TRUNC('MONTH', review_date) AS review_month, ROUND(AVG(rating), 2) AS avg_rating, COUNT(*) AS review_count FROM reviews GROUP BY 1 ORDER BY avg_rating DESC, review_count DESC LIMIT 1` (No `HAVING`) | `SELECT DATE_TRUNC('MONTH', review_date) AS review_month, ROUND(AVG(rating), 2) AS average_rating, COUNT(*) AS review_count FROM reviews GROUP BY 1 HAVING COUNT(*) >= 30 ORDER BY average_rating DESC LIMIT 500` |
| | **Top Result** | August 2026 (avg 4.32, 2,021 reviews) | August 2026 (avg 4.32, 2,021 reviews) with guaranteed sample size floor (`HAVING COUNT(*) >= 30`) |
| | **Rule 11 Enforcement** | Answer included qualitative disclaimers | Pure quantitative answer directly from SQL evidence without unprompted feedback claims |

