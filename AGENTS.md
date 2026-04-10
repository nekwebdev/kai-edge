# repository instructions

- all git commit operations must be run outside the sandbox so the local signing setup can complete successfully
- do not disable commit signing for this repository
- if a task requires creating a commit, request escalated execution for the git commit command instead of committing inside the sandbox
- treat any command that creates a commit object, including `git commit`, `git merge`, `git revert`, and `git cherry-pick --continue`, the same way
- default deploy flow is git-based: commit locally, push to `origin`, then pull on `kai` in `~/kai-edge`
- do not deploy code changes to `kai` via rsync unless explicitly requested by the user
- when pulling on `kai`, preserve local `config.env` values (do not clobber operator settings)
- `kai` rollout branch policy:
  - use local branch `kai-local` on `kai` (tracking `origin/main`)
  - keep operator-specific `config.env` changes committed on `kai-local`
  - update flow on `kai`: fast-forward local `main` from `origin/main`, then rebase `kai-local` onto local `main`
  - bootstrap first-run exception: if `main` is dirty only in `config.env` and `kai-local` does not exist yet, bootstrap migrates that `config.env` delta into a new `kai-local` commit and cleans `main`
  - bootstrap reruns require clean working trees on both `main` and `kai-local`; otherwise bootstrap must fail loudly with cleanup instructions
  - after git-flow enforcement, bootstrap should leave the checkout on `kai-local`
  - bootstrap should enforce this flow when `KAI_GIT_ENSURE_KAI_LOCAL_FLOW=1` (default)
  - do not commit operator-only `config.env` changes back to `origin/main` unless explicitly requested
