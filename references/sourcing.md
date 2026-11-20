# Collecting the stages

## What to collect

- The broadcast cut of the full stage from each music show, one per broadcast date. Each date normally means a different outfit, which is the whole point.
- The studio version of the song as the audio reference and the sound of the mix.
- Four stages are enough for a full mix (measured: 51 face-matched changes in a 3:12 song). More is better, because candidates grow with the number of stage pairs: 11 stages gave about 7000 matched frames.

## What to leave out

| Leave out | Why |
|---|---|
| 직캠 / fancam, 풀캠 / full cam, one-take and choreography cams of a broadcast you already have | Same outfit as that day's broadcast cut, so nothing changes |
| A second upload of the same broadcast (the broadcaster's WORLD or regional channel, licensed platforms, "8D" versions) | One file per broadcast date. Take the domestic K-pop channel's plain stage cut; the longer versions carry intros or interviews |
| Re-aired stages | Re-run programmes (for example Mnet "MUSIC X CURATION") air old stages under a new date |
| Fan re-uploads, "AI upscale", "4K 60fps" remasters | They make an old stage look current, and the picture is processed |
| Other people's stage mixes (교차편집, stage mix, 換裝) | Not a source. Filter them out of search results and do not open them |
| Encore stages, medleys, band or special arrangements, radio lives, relay dance, dance practice | Not the stage, or the arrangement differs from the studio track |

When the user restricts the sources (only this year's stages, only one promotion), obey it and list in the report what was excluded and why. Do not fill the gap with material that breaks the restriction.

## Searching

`yt-dlp` can search and list without downloading:

```bash
P='%(id)s | %(uploader_id)s | %(channel)s | %(duration)s | %(title)s'
NOISE='교차|stage ?mix|換裝|换装|직캠|fancam|풀캠|full ?cam|upscal|remaster|reaction|shorts|lyrics|가사|릴레이|안무|8D|모음'
# general search: romanised and Korean names, with and without each show's name
yt-dlp --no-warnings --flat-playlist --print "$P" "ytsearch30:<group> <song> <show name>" | grep -viE "$NOISE"
# inside a broadcaster's own channel: the way to be sure a show has no further stage
yt-dlp --no-warnings --flat-playlist --playlist-end 40 --print "$P" "https://www.youtube.com/@KBSKpop/search?query=<그룹>"
# one candidate: real upload date, original title, start of the description
yt-dlp --no-warnings --skip-download --print "%(upload_date)s | %(uploader_id)s | %(channel)s | %(duration)s | %(title)s" \
  --print "%(description).300s" -- VIDEO_ID
```

- The `NOISE` filter also hides any title that happens to contain one of its words: adjust it to the song and group.
- `uploader_id` is the channel handle. Broadcaster handles that were checked: `@Mnet` (M COUNTDOWN), `@MnetM2` (M2), `@KBSKpop` (Music Bank), `@MBCkpop` (Show! Music Core), `@SBSKPOP` (Inkigayo), `@TheKPOP` (THE SHOW), `@ALLTHEKPOP` (Show Champion). Other shows turn up through the general search; confirm that the channel is the broadcaster. An occasional `NA` in the channel columns is a hiccup: run the search again.
- **Never read a date off the search list.** Titles there can be machine-translated, which scrambles the YYMMDD broadcast date (261113 was shown as 131126). Decide from the single-candidate lookup: its title is the original and `upload_date` is real. (`--extractor-args "youtube:lang=ko"` also gives original titles in a list, but empties the channel columns.)
- A stage of a song that came back years later can be from the first promotion. Check both the date in the title and the upload date against what the user asked for; an old stage re-uploaded this year fails the first, a re-aired one can pass both.
- Telling a new recording from a re-aired one: the broadcaster's channel has that day's 직캠 / 풀캠 for a new recording and none for a re-run; and a re-run has the same duration as the old upload (compare durations from the single-candidate lookup on both, since the search list can be a second off).
- Keep a list as you go, in `<song>/sources.tsv`: file name, video id, channel, upload date, show, broadcast date. The report needs it, and the file names do not carry the ids.

## The studio track

Use the label's audio-only upload (art track): its description starts with "Provided to YouTube by ...". Not the music video (its audio often has an intro or sound effects), and never an "Inst." version. When several art tracks exist (single, album, repackage), take the release the stages promote; a difference of a second or two is usually silence, and a wrong arrangement shows up in step 3 as weak sync windows for every stage.

## Downloading

```bash
mkdir -p sources
yt-dlp --no-warnings -q -f "137+140/299+140/bv*[height<=1080]+ba[ext=m4a]" --merge-output-format mp4 \
  -o "sources/01_mcd_1015.mp4" -- VIDEO_ID
yt-dlp --no-warnings -q -f "140/ba" -o "sources/00_studio_audio.m4a" -- AUDIO_ID
```

- Format 137 is 1080p at 30 fps, 299 is 1080p at 60 fps. Both work: `prep.py` records each file's frame rate and the mix is 29.97 fps either way.
- Names are `NN_show_MMDD.mp4`, NN = 01, 02, ... in broadcast order. Only the two leading digits matter to the scripts (that is how they tell stages from everything else); the rest is for you, so any short show name will do. `00_studio_audio` is the reference.
- Other people's mixes, when the user wants them analysed, must not start with two digits (for example `mix_<channel>.mp4`), and belong in another folder than `sources/`.
- Afterwards check every file with `ffprobe`: 1920x1080 and a duration that fits one stage.
