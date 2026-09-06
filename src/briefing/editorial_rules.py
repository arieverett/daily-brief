"""Production editorial overrides shared by both newsletter editions."""

from dataclasses import replace

from . import editor

# Allow the editor to use fewer bullets when the paragraph already carries the story.
# This keeps highlights additive instead of forcing repetitive filler.
editor.STORY_SCHEMA["properties"]["highlights"]["minItems"] = 0
editor.STORY_SCHEMA["properties"]["highlights"]["maxItems"] = 3

_QUANTIFIED_RULES_EN = """

Additional Morning Brew-style data and story-selection rules:
- For lead and secondary stories, make the paragraph and bullets complementary, never repetitive. Do not restate a fact in a bullet if the summary already communicates it clearly.
- Use 0-3 bullets, only when they add critical standalone information from the article metadata. Fewer strong bullets are better than filler.
- Every bullet must contain at least one concrete data point written with a numeral and one of these forms when supported by the metadata: a #/count, a % percentage, or a $ monetary figure. Examples include 162,000 jobs, 59%, or $5.85. Never invent or estimate a number just to satisfy this rule.
- Make the summary data-forward too: whenever the candidate metadata contains a meaningful #/count, %, or $ figure, include at least one of the strongest such figures in the paragraph.
- If the metadata contains no trustworthy numeric fact suitable for a bullet, use no bullets rather than repeating prose or manufacturing a statistic.
- Treat a news EVENT or TOPIC, not an individual article URL, as the unit of a main story. Never use two lead/secondary slots for the same underlying event, even when different outlets cover different consequences, updates, locations, agencies, or statistics. Example: an eruption, its flight cancellations, airport closures, school closures, ash warnings, and alternative transport response are ONE story, not several stories.
- When several candidates cover the same event, synthesize the strongest non-duplicative facts into one Morning Brew-style recap. Choose the best candidate as the primary Read more link. Do not fill remaining main-story slots with other coverage of that event. Use those slots for genuinely different topics from the candidate pool.
- Main-story topic diversity is mandatory. Before finalizing each country section, compare the lead and every secondary story by underlying event/entity/action and replace any topical duplicate with the strongest distinct available story. There are enough outlets in the feed; prefer a slightly less prominent distinct story over duplicate coverage.
- Do not repeat a lead/secondary topic again in Speed read. Speed reads must add different topics, not extra angles or consequences of a story already covered above.
- Quick-hit labels describe the SUBJECT CATEGORY, never merely the geography. Use labels such as SPORTS, SOCCER, POLITICS, MONEY, BUSINESS, TECH, CULTURE, MUSIC, FILM, FOOD, TRAVEL, TRANSPORT, SOCIETY, CRIME, WEATHER, or HEALTH as appropriate. For football/soccer fixtures and league news, use SOCCER (or SPORTS when broader). Do not use STOCKHOLM, JAKARTA, MALMO, BANDUNG, or another place name as the label merely because the event occurs there.
- Quick hits remain one concise sentence and do not display highlights, so do not force numeric data into them unless it is genuinely one of the most important facts.
"""

_QUANTIFIED_RULES_ID = """

Aturan data dan pemilihan berita tambahan ala Morning Brew:
- Untuk berita utama dan berita tambahan, paragraf dan bullet harus saling melengkapi, bukan mengulang fakta yang sama.
- Gunakan 0-3 bullet saja, dan hanya jika bullet menambahkan informasi penting yang dapat berdiri sendiri. Lebih sedikit bullet yang kuat lebih baik daripada filler.
- Setiap bullet harus memuat setidaknya satu data konkret dengan angka dan, bila didukung metadata, berbentuk #/jumlah, % persentase, atau $ nilai uang. Contoh: 162.000 pekerjaan, 59%, atau $5,85. Jangan pernah mengarang atau memperkirakan angka hanya untuk memenuhi aturan ini.
- Ringkasan paragraf juga harus data-forward: jika metadata kandidat memiliki angka #/jumlah, %, atau $ yang bermakna, masukkan setidaknya satu angka terkuat ke dalam paragraf.
- Jika metadata tidak memiliki fakta numerik yang tepercaya untuk bullet, gunakan 0 bullet daripada mengulang paragraf atau membuat statistik baru.
- Anggap PERISTIWA atau TOPIK berita, bukan URL artikel, sebagai satu unit berita utama. Jangan pernah memakai dua slot berita utama/tambahan untuk peristiwa yang sama walaupun media berbeda membahas dampak, pembaruan, lokasi, instansi, atau statistik yang berbeda. Contoh: erupsi, pembatalan penerbangan, penutupan bandara, sekolah daring, peringatan abu, dan transportasi alternatif adalah SATU berita.
- Jika beberapa kandidat membahas peristiwa yang sama, gabungkan fakta terkuat yang tidak berulang menjadi satu ringkasan ala Morning Brew. Pilih kandidat terbaik sebagai tautan Baca selengkapnya utama. Jangan isi slot berita utama lainnya dengan liputan lain dari peristiwa tersebut; pilih topik yang benar-benar berbeda.
- Keragaman topik berita utama wajib. Sebelum menyelesaikan bagian Indonesia, bandingkan lead dan semua berita tambahan berdasarkan peristiwa/entitas/aksi yang mendasarinya dan ganti duplikat topik dengan berita berbeda terbaik yang tersedia.
- Jangan ulang topik berita utama/tambahan di Baca kilat. Baca kilat harus menambah topik baru, bukan sudut atau konsekuensi tambahan dari berita yang sudah dibahas.
- Label Baca kilat harus menjelaskan KATEGORI ISI, bukan sekadar lokasi. Gunakan label seperti OLAHRAGA, SEPAK BOLA, POLITIK, EKONOMI, BISNIS, TEKNOLOGI, BUDAYA, MUSIK, FILM, KULINER, WISATA, TRANSPORTASI, SOSIAL, KRIMINAL, CUACA, atau KESEHATAN sesuai isi. Jangan gunakan JAKARTA, BANDUNG, BALI, atau nama tempat lain hanya karena berita terjadi di sana.
- Speed read tetap satu kalimat ringkas dan highlights-nya tidak ditampilkan, jadi jangan memaksakan angka kecuali memang merupakan fakta terpenting.
"""

editor.SYSTEM_PROMPT += _QUANTIFIED_RULES_EN
editor.INDONESIA_SYSTEM_PROMPT += _QUANTIFIED_RULES_ID

# The legacy front_page is not rendered anymore, so it should never block delivery.
# If the model invents only unusable front-page URLs, retry link repair using real
# section stories as compatibility placeholders while keeping all visible content intact.
_original_validate_links = editor.validate_links


def _validate_links_with_hidden_front_page_fallback(edition, candidates):
    try:
        return _original_validate_links(edition, candidates)
    except ValueError as exc:
        if "no usable front page links" not in str(exc):
            raise

        section_stories = []
        if isinstance(edition, editor.Edition):
            section_stories.extend([edition.sweden.lead, *edition.sweden.stories])
        section_stories.extend([edition.indonesia.lead, *edition.indonesia.stories])
        if not section_stories:
            raise

        # Three are enough for the legacy compatibility field; the template never renders them.
        fallback = section_stories[:3]
        if len(fallback) < 3:
            fallback = (fallback * 3)[:3]
        return _original_validate_links(replace(edition, front_page=fallback), candidates)


editor.validate_links = _validate_links_with_hidden_front_page_fallback
