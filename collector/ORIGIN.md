# Origin of this directory

This is the collector that produced the source database of GitHub Stars in
Context, written by Mykhailo Pavlov. It is a snapshot of his repository
`git-h-index` at revision `fe2d7196f4fe305a7a42bb871d267a751f5513f8`
(17 May 2026), published here with his permission under the MIT licence of
this repository. The Git history and CI configuration are not included.

The data were collected between December 2025 and March 2026 with earlier
revisions of the same code, which the author refactored afterwards. We
compared the revisions committed during collection with this one. The
repository query (`ownerAffiliations: OWNER`, `privacy: PUBLIC`,
`isFork: false`, pagination until `hasNextPage` is false), the failure
marker, the row-deletion rule and the follower ranges are the same.

Where to look:

- `data_fetching/users_fetching/popular_users.py`: account discovery
- `data_fetching/github_parser.py`: repository retrieval
- `data_fetching/users_fetching/fetch_user_profile.py`: profile fields
- `data_storage/database.py` and `sql/schema.sql`: storage and schema

`README.md` in this directory is the collector's own documentation. Running
it needs GitHub API tokens supplied through `.env`, as described there.
