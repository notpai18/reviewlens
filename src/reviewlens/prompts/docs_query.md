Turn an analytics question plus SQL findings into a review search.

Question: {question}
SQL findings (compact JSON): {sql_summary}

Return JSON {"query": "...", "game": "..."|null, "app_versions": ["..."]|null, "rating_min": int|null, "rating_max": int|null}.

Use the game, version, or rating segment that stands out in the SQL findings (for example the lowest-rated version). The query must contain only topical content words (1-5 words) for the subject to find in review text. NEVER include generic words such as feedback, opinions, complaints, praise, reviews, players, users, say, mention.
