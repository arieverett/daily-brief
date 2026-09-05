"""Production editorial overrides shared by both newsletter editions."""

from . import editor

# Allow the editor to use fewer bullets when the paragraph already carries the story.
# This keeps highlights additive instead of forcing repetitive filler.
editor.STORY_SCHEMA["properties"]["highlights"]["minItems"] = 0
editor.STORY_SCHEMA["properties"]["highlights"]["maxItems"] = 3

_QUANTIFIED_RULES_EN = """

Additional Morning Brew-style data rules:
- For lead and secondary stories, make the paragraph and bullets complementary, never repetitive. Do not restate a fact in a bullet if the summary already communicates it clearly.
- Use 0-3 bullets, only when they add critical standalone information from the article metadata. Fewer strong bullets are better than filler.
- Every bullet must contain at least one concrete data point written with a numeral and one of these forms when supported by the metadata: a #/count, a % percentage, or a $ monetary figure. Examples include 162,000 jobs, 59%, or $5.85. Never invent or estimate a number just to satisfy this rule.
- Make the summary data-forward too: whenever the candidate metadata contains a meaningful #/count, %, or $ figure, include at least one of the strongest such figures in the paragraph.
- If the metadata contains no trustworthy numeric fact suitable for a bullet, use no bullets rather than repeating prose or manufacturing a statistic.
- Quick hits remain one concise sentence and do not display highlights, so do not force numeric data into them unless it is genuinely one of the most important facts.
"""

_QUANTIFIED_RULES_ID = """

Aturan data tambahan ala Morning Brew:
- Untuk berita utama dan berita tambahan, paragraf dan bullet harus saling melengkapi, bukan mengulang fakta yang sama.
- Gunakan 0-3 bullet saja, dan hanya jika bullet menambahkan informasi penting yang dapat berdiri sendiri. Lebih sedikit bullet yang kuat lebih baik daripada filler.
- Setiap bullet harus memuat setidaknya satu data konkret dengan angka dan, bila didukung metadata, berbentuk #/jumlah, % persentase, atau $ nilai uang. Contoh: 162.000 pekerjaan, 59%, atau $5,85. Jangan pernah mengarang atau memperkirakan angka hanya untuk memenuhi aturan ini.
- Ringkasan paragraf juga harus data-forward: jika metadata kandidat memiliki angka #/jumlah, %, atau $ yang bermakna, masukkan setidaknya satu angka terkuat ke dalam paragraf.
- Jika metadata tidak memiliki fakta numerik yang tepercaya untuk bullet, gunakan 0 bullet daripada mengulang paragraf atau membuat statistik baru.
- Speed read tetap satu kalimat ringkas dan highlights-nya tidak ditampilkan, jadi jangan memaksakan angka kecuali memang merupakan fakta terpenting.
"""

editor.SYSTEM_PROMPT += _QUANTIFIED_RULES_EN
editor.INDONESIA_SYSTEM_PROMPT += _QUANTIFIED_RULES_ID
