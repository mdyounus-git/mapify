# Phase 10 — OCR and script-adaptive reading

**State:** implemented · commits `a0c6179`, `96a20f3`

Text on a sign is the strongest clue a photograph can carry, because a place
name can be *checked* against the gazetteer rather than guessed. This phase
made that channel actually work.

---

## Goal

Read the writing that is in the image, in whatever script it is in, and use
what it says.

## What was built

**EasyOCR (Apache-2.0)** as an optional engine — it pulls about 300 MB of
dependencies, so it is installed on request rather than by default, and the
tool reports which clue types are unavailable without it rather than pretending
the image had no text.

**Script-adaptive reader selection.** The pipeline picks the recogniser from
the writing system the zero-shot pass identified, for that image only.

**Latin-language reader selection.** The English model does not merely fail on
diacritics, it flattens them, so a peaked country prior can also select a
language-specific Latin recogniser.

**Language hints wired into fusion.** `script_detect` had produced
`language_hints` from marker characters since Phase 01 — `ğ ş ı` for Turkish,
`ł ż` for Polish, `ß` for German — and nothing consumed them. Now weighted by
how many countries share the marker, so a rare one counts for more than `ç`.

## Verification

On a rendered Cyrillic street sign, the whole evidence chain closing:

```
english only : MOCKBA                      -> scored as Latin 100%
with ru+en   : МОСКВА | Тверская улица     -> Cyrillic 53%, Latin 47%
               TOPONYM 'МОСКВА' -> Moscow (NAME_RU, Russia)
```

On the real Bengaluru sample, the Kannada reader took it from 15 garbled lines
to 24 including ಮೆಟ್ರೋ. On the Istanbul sample, `MECBURİ İSTİKAMET OTOBÜSLERİ`.

**On the eleven-sample benchmark, OCR changed no verdict.** The images it read
were already placed correctly and the two failures have no legible text at all.
That is recorded in the README rather than glossed, because it is the honest
scope of the feature: it earns its place on photographs carrying a readable
place name, not as a general accuracy boost.

26 tests.

## What went wrong

Three things, all found by running OCR against real photographs rather than
reasoning about it:

- **An English-only recogniser does not return nothing on a Cyrillic sign, it
  returns Latin lookalikes.** "МОСКВА" comes back as "MOCKBA", which script
  detection scores as 100% Latin, and fusion then uses that to push *away* from
  Russia. Reading the wrong thing is worse than reading nothing.
- **The reader was only selected when the script reading cleared the threshold
  for scoring a candidate.** Those are different decisions: loading the wrong
  recogniser costs one model load, while letting a reading move a score is a
  claim about where a photograph was taken. Selection now happens at 0.10 and
  looks past the winner, because Latin wins almost any mixed sign and the
  runner-up is the informative answer.
- **A bug of my own from an hour earlier**: `GroupReading.runners_up` holds
  display labels for the report, and the reader selection was passing those to
  a lookup keyed by ISO 15924 code, so it could never have matched. Option keys
  now travel in their own `ranked` field.

## A note on circularity

Letting the model's own guess pick the reader is not circular. Loading the
Turkish recogniser does not invent Turkish letters: if the sign says MAIN
STREET it still reads MAIN STREET and yields no hint. The guess decides where
to look; the image decides what is found. The docstring on
`_choose_ocr_group()` says so, because it is the kind of thing a reader should
be able to check rather than take on trust.

## Left open

- EasyOCR has no Greek or Hebrew recogniser, so those scripts get the English
  reader and the mapping says so explicitly rather than loading something
  useless.
- One extra reader per image. A full multi-script sweep is available through
  `ocr_extra_groups` but is slow enough to stay opt-in.
