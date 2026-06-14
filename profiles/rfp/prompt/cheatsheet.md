DISCOVER  RFP pursuit (§4.2): read client docs + our materials + POC/example SF orgs + your own SF knowledge
          LABEL every claim: CLIENT REQUIRES / OUR MATERIAL SAYS / OUR POC SHOWS / EXAMPLE PROJECT SHOWS / SALESFORCE (general) / MY SUGGESTION
          jobs: requirement→SF write-up · client gaps · our gaps · questions to ask · demo+POC-polish · commercial register
          doc mention = lead · POC/example org = proof (name it) · SALESFORCE(general) = your knowledge, to verify · no evidence = gap
          Excel cell values aren't searchable → rfp.read_workbook(lib, "docs:<path>") / rfp.read_table(lib, id, "Sheet")
deck wanted (on demand): from librarian.skills import pptx_draft → list_themes/match_skeletons → plan.json → validate_plan → compose(..., theme=<real id>, lib=lib)
          data you hold → real chart/table slot; only true raster (photo/logo/screenshot/diagram) → ppt.placeholder("describe what to paste") in an IMAGE slot (never bare, never a text slot)
          offline+no vision: you/app/layer NEVER add images — the human pastes them later; ignore SKILL.md find-asset & /api/asset/add (no host) even if reader.py suggests them
          after compose: reply FINISH-THIS-DECK — .pptx path + swap each grey box (label=what) + fill every <TBC> (list inline) + see .warnings.json; render needs build --pptx; full contract pptx/SKILL.md