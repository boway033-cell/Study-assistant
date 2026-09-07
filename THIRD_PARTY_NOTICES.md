# Third party notices

The project's original code remains under its existing MIT license. Third-party
materials below retain their own licenses; the root MIT license does **not**
relicense them. The combined official-document feature includes noncommercial
material and must not be represented as an MIT-only distribution.

## lieflat-gongwen

- Source: https://github.com/larashero3-dotcom/lieflat-gongwen
- Revision: `e0a5aba6b6ce66402aff799ea213af2d987d99dd`
- License: **PolyForm Noncommercial 1.0.0**.
- Location: `backend/app/services/official_skills/lieflat/`.
- Included: original skill instructions, parameter card and `scripts/check_params.py`.
- License text: [lieflat LICENSE](backend/app/services/official_skills/lieflat/LICENSE).
- Scope: outline-confirmation workflow and actual statistical diagnostic engine;
  this integration does not bundle or claim to use the full exemplar corpus/DNA.
- Local changes: package initializer files only; checker logic is unchanged.

Review the full license before use or redistribution, especially for commercial
purposes. This project does not grant additional permission on the author's behalf.

## official-document-skill

- Source: https://github.com/Liuxiangjian-ai/official-document-skill
- Revision: `cbe5f8cd8aa79c8d977ff858994780f306cebb66`
- Copyright (c) 2026 Xiangjian Liu. License: MIT.
- Location: `backend/app/services/official_skills/official_document/`.
- Included: original `SKILL.md`, loaded as the drafting/review reference, and
  [original MIT license](backend/app/services/official_skills/official_document/LICENSE).
- Local changes: none to the instructions.

## sanmu-document-formatting

- Source: https://github.com/Triwood-79/sanmu-document-formatting
- Revision: `3c42e08cd7980ea7c1212faf782997806d3b0899`
- Copyright (c) 2026 Triwood-79. License: MIT.
- Location: `backend/app/services/official_skills/sanmu/`.
- Included: original skill instructions, format/privacy references, generic
  official preset, common utilities, inspection, DOCX engine, metadata scrubber
  and structural validator; [original MIT license](backend/app/services/official_skills/sanmu/LICENSE).
- Local changes: sibling imports converted to package-relative imports and
  package initializer files added. The engine's formatting logic is unchanged.
- The app passes an explicit profile and does not access or modify user-wide skill
  state. Additional validation and persistence live in the application adapter.

Third-party skill text is reference material, not authority to execute embedded
shell commands. User materials are not treated as instructions to run tools.
