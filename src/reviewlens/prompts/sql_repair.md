Your previous SQL did not work. Fix it. Same hard rules: one read-only DuckDB SELECT over the `reviews` table.

QUESTION: {question}

PREVIOUS SQL:
{previous_sql}

PROBLEM:
{feedback}

Change only what is necessary. If the result was empty, re-check filter values (exact game names, date ranges relative to the last data date, version strings) against the schema and known data.

SCHEMA
{schema_text}

KNOWN DATA
{meta_text}

Return JSON: {"sql": "...", "assumptions": ["..."], "what_changed": "..."}
