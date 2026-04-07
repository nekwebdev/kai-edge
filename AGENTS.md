# repository instructions

- all git commit operations must be run outside the sandbox so the local signing setup can complete successfully
- do not disable commit signing for this repository
- if a task requires creating a commit, request escalated execution for the git commit command instead of committing inside the sandbox
- treat any command that creates a commit object, including `git commit`, `git merge`, `git revert`, and `git cherry-pick --continue`, the same way
