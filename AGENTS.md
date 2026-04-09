# repository instructions

- all git commit operations must be run outside the sandbox so the local signing setup can complete successfully
- do not disable commit signing for this repository
- if a task requires creating a commit, request escalated execution for the git commit command instead of committing inside the sandbox
- treat any command that creates a commit object, including `git commit`, `git merge`, `git revert`, and `git cherry-pick --continue`, the same way
- default deploy flow is git-based: commit locally, push to `origin`, then pull on `kai` in `~/kai-edge`
- do not deploy code changes to `kai` via rsync unless explicitly requested by the user
- when pulling on `kai`, preserve local `config.env` values (do not clobber operator settings)
