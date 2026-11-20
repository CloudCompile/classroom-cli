# classroom

[![tests](https://github.com/CloudCompile/classroom-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/CloudCompile/classroom-cli/actions/workflows/tests.yml)

Google Classroom assignments, missing work and class materials in your
terminal — built like `skyward` and `msl`, but through a browser, because
this account can't create an OAuth client.

```console
$ classroom missing
reading as student@example.com
missing
Due                    Assignment               Class                     Points
Tue Nov 17 · 1d late   Persuasive Essay Draft   English 9
                       (revision)
Thu Nov 12 · 6d late   OPTIONAL 2.7-2.9         Geometry Honors
                       Section Check
Fri Nov 13 · 5d late   Edpuzzle: Rock Cycle     Environmental Science - 7
10 items
```

## Commands

| command | shows |
| --- | --- |
| `classroom login` | opens the classroom Chrome to sign in (once) |
| `classroom whoami` | which account the profile is using |
| `classroom courses` | your classes, section, teacher, next due |
| `classroom todo [--due] [--detail]` | Classroom's To-do tab |
| `classroom missing [--detail]` | work marked **not turned in** |
| `classroom done [-n N]` | turned-in work (Classroom's Done tab) |
| `classroom classwork -c <course>` | posted materials and work for one class |
| `classroom show -t <title>` | points, due date, attachments, state |
| `classroom attachments -c <course> -m <item> [--read]` | the Drive files behind a classwork item |
| `classroom read -c <course> -m <item>` | the **text** of an attachment (docs, PDFs) |
| `classroom parse -c <course> -n N` | a teacher's weekly schedule → structured assignments |
| `classroom logout --reset` | delete the profile (signs out) |

`courses`, `todo`, `missing`, `done`, `classwork` and `show` take `--json`.

`--detail` opens each assignment page for points/attachments — accurate but
slow (one page load per item), so it's opt-in.

## The account problem, and the guard

Classroom content is per-Google-account, and sessions live in a Chrome
profile. A profile that also holds a personal Gmail can quietly answer as
the wrong person, and nothing in the URL says which account replied.

So this uses a **dedicated profile** (`~/.config/classroom-chrome-profile`)
that should contain only the school account, and every command starts with an
identity check:

```
$ CLASSROOM_ACCOUNT=someone@gmail.com classroom courses
refusing to continue: the classroom profile is signed in as
student@example.com, but only someone@gmail.com is allowed.
exit 4
```

The pinned account lives in `~/.config/classroom/config`:

```
account=student@example.com
```

Reading is refused (exit 4) if the browser is signed in as anything else,
and refused (exit 3) if no account is pinned at all — so adding a second
account to that profile can never silently leak into the output.

## Signing in

```console
$ classroom login
```

opens Chrome on display `:1` (the VNC desktop) with the dedicated profile.
Sign in **as the school account only**, and if Chrome offers to trust the
device, allow it — otherwise the session won't survive a headless restart.
Then `classroom whoami` should print your school address.

After that, `classroom` can drive the profile headless.

## How it reads Classroom

Classroom is a single-page app. Both of its structured interfaces are
hostile to automation — the internal API is `batchexecute` RPC with opaque
request ids and positional JSON, and most CSS class names are hashed
(`MHxtic`, `XQMoHb`) and change without notice.

So the readers anchor on the things that are *meaningful* instead:

| what | anchor |
| --- | --- |
| classes | `li[data-course-id]` + `a[href^="/c/"]` |
| to-do / missing / done | `a[href*="/a/"][href*="/details"]` |
| classwork rows | `[jsname="tdoU3e"]` |
| detail page | `document.title`, leaf text, Drive links |

Assignment URLs (`/c/<course>/a/<assignment>/details`) are stable and are
what everything keys on.

Because it's an SPA, navigating and reading immediately returns the
*previous* view. Each reader navigates, waits for the expected content to
appear, then reads.

## Reading attachments

Teacher files are usually view-only, and Google's export/preview routes hand
back an empty document model for them. What *does* work is
`drive.google.com/file/<id>/view`, which renders the page as real text — so
`classroom read` gets the full text of a Doc or PDF.

Finding the file takes a click: the Drive link only appears once the
classwork row is opened, so the CLI clicks the row and reads the links that
appear.

```console
$ classroom read -c geometry -m "Week 13"
Week 13: 11-16-26
Geometry Honors Schedule S1 Week 13
Week of November 16, 2026
...
Monday 11/16  4.3 SSS & SAS  Due Tuesday:  pg.305 # 5, 6, 11, 12, 16-19, 22, 24, 36, 38
```

## Parsing weekly schedules

`classroom parse` turns those documents into assignments with due dates:

```console
$ classroom parse -c geometry -m "Week 13"
Week 13: 11-16-26  ·  week of Nov 16  ·  1st Semester
Due                   Assignment                     Listed under
Tue Nov 17 · 1d late  4.3 SSS & SAS                  Mon 11/16
Wed Nov 18 · today    4.3 Proof Packet 1             Tue 11/17
...
needs review          4.1-4.4 Check for Learning...  Fri 11/20
1 entry needs a human look
```

Teacher schedules are hand-typed and inconsistent — "Due Tuesday:",
"Due: Friday", "Both Due at the START OF CLASS on Wednesday" (with the
weekday on the *next* line), or no due date at all. The parser only emits a
date it can prove; everything else is marked `needs_review` with a reason
rather than guessed. A wrong homework date in a planner is worse than an
entry asking for a look.

## Dates

Classroom renders due dates inconsistently: `Saturday, 5:59 AM`,
`Yesterday, 5:59 AM`, `Nov 13`, `Fri, Nov 13`, `11/21/2026`. A bare weekday
means the most recent one. Anything unparseable returns no date rather than
a wrong one, and two-digit years roll forward sensibly.

## Tests

```console
$ python3 -m pytest
```

81 tests, all offline:

- parsers against **snapshots captured from the live pages**
  (`tests/fixtures/pages.json`), including regressions that bit during
  development — the teacher column showing the section, the detail title
  showing nav chrome, duplicated classwork dates;
- due-date parsing across every format above;
- the identity gate, including that a personal account and an unpinned
  profile are both refused, and that the probe never clicks the avatar
  (clicking triggers a mid-evaluation navigation);
- weekly-schedule parsing over three real teacher documents, chosen because
  they're the messy ones (no due markers, due dates wrapping onto the next
  day's heading, a typo in the source that is preserved rather than
  corrected).

## Notes

- Unofficial; no affiliation with Google. It reads the same pages you'd see.
- Not implemented: turning work in, posting comments, grades. Grades only
  appear after a teacher returns work, and some districts hide them — the
  parsers look for points and letter grades but will not invent them.

## See also

- [skyward-cli](https://github.com/CloudCompile/skyward-cli) — grades and assignments from the gradebook
- [msl-cli](https://github.com/CloudCompile/msl-cli) — the MyStudyLife planner, including the sync that fills it
- [classroom-cli](https://github.com/CloudCompile/classroom-cli) — Classroom posts, classwork and attachments
- [agenda-cli](https://github.com/CloudCompile/agenda-cli) — all of the above in one view
- [gsuite-cli](https://github.com/CloudCompile/gsuite-cli) — the school Google account's mail, drive, docs and calendar

## License

MIT — see [LICENSE](LICENSE).
