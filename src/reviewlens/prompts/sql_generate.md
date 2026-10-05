Write ONE read-only DuckDB SQL query that answers the question.

HARD RULES
- Output a single SELECT (CTEs allowed). Never modify data. No comments, no semicolons.
- Only the table and columns in the schema. Bare table name `reviews`.
- Game names must match the known games exactly. Relative dates ("last 30 days", "recently") are relative to the game's last data date (given below), not today's date.
- app_version is often NULL; exclude NULLs when grouping by version, and say so in assumptions.
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
