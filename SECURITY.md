# Security policy

This repository is a documentation-and-template framework — it ships no
running service, and the workflows under `template/` execute only after
an adopter copies them into their own repo. Two report classes still
matter here:

* **A vulnerability in the framework itself** — a template workflow that
  mishandles a secret, an injection path through the skill's inputs, a
  bypass of the severity/labeling path, a scanner escape.
* **A suspected sanitization escape** — anything in this tree that looks
  like it identifies the private production system this was extracted
  from (see [PORTING.md](PORTING.md)).

Report either **privately**, via the "Report a vulnerability" button on
this repository's Security tab (GitHub private vulnerability reporting).
Please don't open a public issue with the details — for a sanitization
escape especially, a public issue would amplify exactly the thing that
needs removing.

This is a solo-maintained project; expect an acknowledgment within a few
days.
