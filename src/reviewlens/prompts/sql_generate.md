Write ONE read-only DuckDB SQL query that answers the question.

HARD RULES
- Output a single SELECT (CTEs allowed). Never modify data. No comments, no semicolons.
- Only the table and columns in the schema. Bare table name `reviews`.
- Game names must match the known games exactly. Relative dates ("last 30 days", "recently") are relative to the game's last data date (given below), not today's date.
- app_version is often NULL; exclude NULLs when grouping by version, and say so in assumptions.
- One game per version: version numbers belong to a specific game. When grouping by version (`app_version`), always include `game` in `SELECT` and `GROUP BY` (e.g. `GROUP BY game, app_version` or filter by game if the question specifies one). Never group by `app_version` alone across multiple games.
- Minimum sample size for rankings: when ranking by an average, ratio, or percentage (ORDER BY AVG/ratio), always require a minimum sample size with `HAVING COUNT(*) >= 30` (or the threshold specified in the question), and include `COUNT(*)` in the `SELECT` list so sample sizes are clear. Never rank small noisy samples without HAVING.
- Text matching: use content ILIKE '%word%' (case-insensitive). For short words that also occur inside other words (e.g. "ads" in "loads"), match whole words with regexp_matches(content, '\bads?\b', 'i').
- Percentages: multiply by 100 and ROUND(..., 2). Alias every output column clearly.
- Add ORDER BY for rankings and time series. Prefer aggregated results; if returning rows, keep them few.

SCHEMA
{schema_text}

KNOWN DATA
{meta_text}

EXAMPLES
{fewshot_text}

QUESTION: {question}

Return JSON: {"sql": "...", "assumptions": ["..."]}
