# What makes these Reels watched - research notes (October 2026)

How the Reel format was chosen. Evidence quality varies a lot: platform statements and regulator data are solid; most
"Reels best practice" numbers come from vendor blogs and are marked as weak.

## 1. Review of the first Reel (10 Oct 2026, 87 s)

| What | Found | Change |
|---|---|---|
| Length | 87 s, five unrelated parts (compounding lesson, 52-week-low test, ICICI allotment news, takeaway, 9 s disclaimer) | ONE topic per Reel, 45-60 s |
| Hook | "Paisa bhi paisa kamata hai?" under a "KYA AAP JAANTE HO?" label | first beat = bold question / shocking number, no label |
| Visual change | the same big text for 15-30 s per part; only the caption changed | a new picture every beat (one or two sentences) |
| Captions | 6-word chunks, cut mid-phrase | word-by-word, current word highlighted, timed to the voice |
| Pauses | 0.35-0.7 s of silence every 1.5-4 s (each part voiced separately) | silences trimmed to a 0.15 s breath |
| Loudness | -20.4 LUFS integrated (quieter than most phone video, ~-14) | normalised to -14 LUFS |
| Numbers | "2536" read wrongly | every number spelled out for the voice (Hindi words in Devanagari mode) |
| News | a routine ESOP share allotment to employees, filed as "fund raising" - nothing a viewer can learn from | routine allotments skipped; news types with a consistent past reaction preferred |
| End | 9 s spoken disclaimer, no reason to comment or come back | short spoken disclaimer (full text on screen + caption), a comment question, tomorrow's teaser, episode numbers |

The voice itself could not be judged here (no audio playback in the build environment); the pacing, loudness and pauses
were measured with ffmpeg.

## 2. What Instagram ranks

- Instagram's head, Adam Mosseri (Jan 2025): the three signals that matter most are **watch time, likes per reach and
  sends per reach**; likes weigh a little more with followers, sends a little more with non-followers.
  [Social Media Today](https://www.socialmediatoday.com/news/instagram-shares-algorithm-2025/738034/) - solid.
- Originality: accounts that mostly repost others' material drop out of recommendations; new narration and graphics
  count as original. [eMarketer](https://www.emarketer.com/content/instagram-s-algorithm-clamps-down-on-repurposed--unoriginal-photos-posts),
  [PetaPixel](https://petapixel.com/2026/04/30/new-instagram-policies-target-reposted-content/) - solid. Our Reels are
  original (own research, own script) - keep it that way.
- AI label: accounts built around an AI *persona* must carry the "AI-generated profile" label or lose reach.
  [MediaPost](https://mediapost.com/publications/article/417580/instagram-limits-reach-for-creator-profiles-withou.html) -
  one report. We have no AI persona, but an AI voice: use Instagram's "AI info" label when posting to be safe.
- Trial Reels (accounts with 1,000+ followers) show a Reel to non-followers first - useful to test hooks.
  [Social Samosa](https://www.socialsamosa.com/news-2/instagram-introduces-wider-access-trial-reels-9493132).

## 3. Length

- Socialinsider, ~140k business Reels: **45-60 s** had the highest engagement (0.35%) and median views; >3 min fell to
  0.15%. Via [Klap](https://klap.app/blog/how-long-should-an-instagram-reel-be) - second-hand, moderate.
- Shorter Reels (7-15 s) finish more often, but teach less; a smaller study found 15-30 s best per view.
  [Shortimize](https://www.shortimize.com/blog/video-length-sweet-spots-tiktok-reels-shorts) - weak, conflicting.
- Decision: 45-60 s (120-160 spoken words) - long enough to explain one idea, short enough to finish.

## 4. Language

- **SEBI Investor Survey 2025** (~90,000 households): **47% want financial education in Hindi, 47% in a regional
  language, 5% in English**; social media is the top channel for financial education (70%).
  [Moneylife](https://moneylife.in/article/just-1-in-10-indian-households-invests-in-stocks-finfluencers-driving-retail-trends-sebi-survey/78471.html),
  [SEBI report](https://www.sebi.gov.in/sebi_data/commondocs/jan-2026/Investor%20Survey%202025%20Main%20Report.pdf) - solid.
- New investors: Uttar Pradesh added the most in July 2025; the top states (UP, Maharashtra, Gujarat, Tamil Nadu,
  West Bengal) are ~45% of new registrations; 56% of investors are under 30.
  [NSE Market Pulse Aug 2025](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Market%20Pulse_Aug%202025.pdf),
  [Outlook Money](https://www.outlookmoney.com/invest/equity/new-investor-registrations-on-nse-at-six-month-high-in-july-know-which-states-led-the-surge) - solid.
- Indian finance creators: Hindi + English mixed is the most common medium (~48% of 48 creators studied).
  [CFA Institute via Outlook Money](https://www.outlookmoney.com/personal-finance/only-6-of-finfluencers-are-sebi-registered-yet-33-offer-explicit-stock-recommendations-report) - small sample.
- By sector: general video consumption in India is Hindi-first (Google: 54% Hindi, 16% English) and regional languages
  dominate news; no source splits finance vs tech vs entertainment by language.
  [Social Samosa / Google](https://www.socialsamosa.com/2020/06/google-report-one-in-every-three-indians-watch-online-videos),
  [exchange4media](https://www.exchange4media.com/industry-briefing-news/the-changing-'lingual'-face-of-digital-india.-88267.html) - old (2019-20), weak for finance.
- Script for captions: one vendor survey found ~58% prefer reading Hindi in Roman letters, 13% in Devanagari.
  [Milestone Localization](https://www.milestoneloc.com/hinglish-report-pr/) - weak.
- Decision: **Hindi-first Hinglish** voice (Hindi words in Devanagari for the voice so they are pronounced as Hindi),
  **Roman-letter captions**. Regional-language versions (Marathi, Gujarati, Bengali, Tamil) are the next step once the
  Hindi account has a base - the survey says half the audience wants them.

## 5. "Masala" - how far an unregistered educator can go

- SEBI's Jan 2025 circular: education may not include advice or performance claims, and may use market prices only
  with a 3-month lag. [Business Standard](https://www.business-standard.com/markets/news/sebi-finfluencer-circular-live-stock-data-market-education-rules-125013000571_1.html) - solid.
- Enforcement: in Dec 2025 SEBI barred a trading educator and impounded over ₹546 crore - giving entry and exit points
  made it advice, not education. [NLIU Law Review](https://nliulawreview.nliu.ac.in/blog/sebis-crackdown-on-finfluencers-a-legal-and-regulatory-perspective) - solid.
- What successful Indian finance creators do: humour, relatable analogies, storytelling, real-life scenarios.
  [idiotic.media](https://idiotic.media/financial-influencers-in-india/), [ebizfiling](https://ebizfiling.com/blog/top-5-finance-influencers-in-india-you-should-know/) - descriptive, no data.
- Decision - safe masala: myth-busting, twists, true stories of settled scams and crashes, desi analogies, humour,
  rhetorical questions, comment questions. Not used: tips, "kal ka stock", return claims, live prices, our own
  strategy's backtest returns (removed from the Reel facts so nothing reads as a performance claim).

## 6. Captions, music, numbers

- Word-by-word captions: vendors claim large retention gains; independent evidence only says captions help completion.
  [Opus](https://www.opus.pro/blog/best-caption-presets-styles-boost-retention) - weak, but cheap to do.
- Music: business accounts only get Meta's commercial library (~14k tracks); creator accounts get the full library.
  Add a soft track in the Instagram app at low volume when posting (the video ships without music).
  [Soundstripe](https://www.soundstripe.com/instagram) - moderate.
- Numbers: ElevenLabs says its models can misread numbers and recommends writing them out in words.
  [ElevenLabs help](https://elevenlabs.io/docs/help-center/product/core-capabilities/text-to-speech/why-are-numbers-dates-symbols-and-acronyms-not-properly-pronounced-or-spoken-in-the-correct-language) - solid.

## 7. Return viewers

No platform data was found on what makes viewers come back. The format uses the usual devices - numbered series, a
fixed time each day, an open question, tomorrow's teaser - and the Instagram Insights numbers after 2-3 weeks (average
watch time, sends per reach, returning viewers) should decide what stays.

## 8. How the Reel looks (11 Oct 2026)

The first Reels were "plain text in each frame": a still gradient, a big line of text and a caption box per beat. What
the research says about the picture:

- **Text-only frames rank lower.** Instagram's creator guidance says Reels "predominantly covered by text" are less
  likely to be recommended. [Tubefilter](https://tubefilter.com/2021/02/10/instagram-reels-with-tiktok-watermark-less-discoverable/) - moderate.
  Professional explainers (Bloomberg Quicktake, WSJ, Think School, CNBC-TV18) always put a real visual - footage, a
  chart, a screenshot, an icon - under a short headline.
- **Moderate pace beats frantic pace.** Across 1,200 short videos (validated on ~14,500), engagement rose with the
  intensity of cuts, motion and sound up to a point and then fell (an inverted U).
  [arXiv 2604.19995](https://arxiv.org/pdf/2604.19995) - solid. Editing pace and colourfulness correlate with
  engagement on TikTok. [J. Business Research 2025](https://www.sciencedirect.com/science/article/abs/pii/S0148296325004850) - correlational.
- **Captions** add view time (Facebook: ~12%) and most people watch muted in public.
  [3Play](https://www.3playmedia.com/blog/captions-increase-viewership-for-facebook-video-ads/) - moderate.
- **Safe zone**: Instagram covers the top ~270 px, the bottom third (username, caption) and ~130 px on the right
  (buttons); the profile grid shows the middle 1080x1440. [adsuploader](https://adsuploader.com/blog/meta-ads-safe-zones),
  [Hopper](https://www.hopperhq.com/blog/instagram-reel-size/) - moderate (Meta publishes no organic numbers).
- **Sound effects and music**: no good evidence they lift retention; used sparingly. Zoom punches, emojis, kinetic
  type: common craft, no published lift numbers - weak.
- **Faces**: photos with faces get ~38% more likes (1.1 M Instagram photos, correlational). We have no face; a
  consistent icon per beat is the stand-in.

What changed in `render.py` (30 frames a second, streamed into ffmpeg):

| Before | Now |
|---|---|
| still gradient | background with soft glows and a dot grid that drifts slowly |
| one big line of text | a big icon per beat (chosen by Claude from ~120 Noto emoji) that pops in and floats; the headline's words pop in one by one, numbers and one *starred* key word in colour |
| numbers as text | a beat that is just a number counts up to it (with a soft "ding") |
| market facts as text | MARKET AAJ: the Nifty's last 60 sessions as a line that draws itself, sectors and FII/DII as bars that grow (index level only, the day's NSE data) |
| 4-word caption box | 3 words at a time in heavy outlined letters, the current word in yellow with a small pop |
| hard cuts | a quick zoom-settle and a soft whoosh on each new beat; a short shake on the twist |
| DejaVu font | Poppins Black / ExtraBold (Open Font License) |
| text down to y=1500+ | everything important inside x 70-950, y 270-1300 |

Cost: ~15 ms a frame here (about a minute of rendering for a 60 s Reel; roughly twice that on the 2-core server).
Next step if Insights show viewers still skip early: real B-roll footage under the headline from the free Pexels API
(needs a free key; Pexels' licence allows commercial use without credit).

## 9. Audience psychology and growth (11 Oct 2026)

Full report with sources: `reports/Short form finance audience growth.md` (notes in
`research_notes/Short form finance audience growth/`). In short: Indian viewers fear losing money more than they want
to get rich (SEBI survey 2025: ~80% prefer protecting capital); they learn from friends and family (59%) and send
useful Reels to them - and sends per reach is the signal Instagram says matters most for non-followers. Our first five
Reels ran too long (53-104 s) and had no recurring "who"; every big Indian finance creator shows a face, and the
faceless accounts that grew built a character. Hence the changes listed in README (length governor, hook and re-hook
rules, spoken send line, source tags, drawn cast with an A/B test by week).
