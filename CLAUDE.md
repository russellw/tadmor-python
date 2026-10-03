The goal of this project is to develop comprehensive business management software.
It is the Python counterpart of tadmor (~/tadmor): the same product, specified by
tadmor's spec/ and checked by its conformance/ suite, built on a different stack so
the two can be compared (see ~/tadmor/docs/counterpart-metrics.md).

Technology stack:
Postgres for the database, using the shared schema from tadmor's db/migrations.
Python and Django for the back end.
Server-rendered Django templates for the user interface; no npm, no JavaScript build.
See docs/stack.md for the decision and its rationale.

Schema design:
The schema is shared with tadmor and is not ours to redesign. Django models map onto
it with managed = False; Django's own migration system is not used for it.

Dependencies:
Supply-chain conscious throughout; keep the third-party footprint small, pinned, and
reviewable in-repo. The only permitted third-party packages are those listed in
docs/stack.md. New packages need a conversation first. All third-party source is
vendored and committed; nothing is installed from PyPI at build or run time.

Working on it:
Business rules live in tadmor/services/, shared by the JSON API (tadmor/api/)
and the HTML UI (tadmor/ui/). Never put a rule in a view.
spec/, conformance/, and db/migrations/ are copies from tadmor (spec/UPSTREAM);
never edit them here. Re-export from tadmor with spec/export.sh.
Before committing, run `make test` and `make conformance`; both must pass.

Version control:
Commit directly to the default branch. Do not create feature branches.
