---
name: "create-pr"
description: "Create a GitHub pull request from the current branch"
whenToUse: "When the user wants to create a PR, or after committing changes that should be reviewed"
arguments: "title?:string"
---

# Create Pull Request

Create a GitHub pull request for the current branch.

## Step 1: Understand the Branch

Run these commands to understand the current state:

1. `git status` — check for uncommitted changes
2. `git branch --show-current` — get current branch name
3. `git log --oneline main..HEAD` — see all commits that will be in the PR (adjust base branch if needed)
4. `git diff main...HEAD --stat` — see file-level summary of all changes

If there are uncommitted changes, ask the user whether to commit first.

## Step 2: Draft the PR

Analyze ALL commits in the branch (not just the latest), then draft:

- **Title**: under 70 characters, imperative mood
- **Body**: use this format:

```
## Summary
<1-3 bullet points summarizing the changes>

## Test plan
<bulleted checklist of how to verify the changes>
```

$ARGUMENTS

## Step 3: Push and Create

1. Push the branch: `git push -u origin HEAD`
2. Create the PR: `gh pr create --title "..." --body "..."`
3. Return the PR URL to the user

## Important Rules

- Never force-push unless the user explicitly asks
- If the branch is `main` or `master`, warn the user and ask for confirmation
- Include all relevant context in the PR body — reviewers shouldn't need to read commit messages
