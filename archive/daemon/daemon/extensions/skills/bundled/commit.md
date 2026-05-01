---
name: "commit"
description: "Create a well-structured git commit from current changes"
whenToUse: "When the user asks to commit, or when code changes are complete and ready to be committed"
arguments: "message?:string"
---

# Git Commit

Create a git commit for the current changes. Follow these steps carefully.

## Step 1: Understand the Changes

Run these commands to understand what will be committed:

1. `git status` — see all untracked and modified files
2. `git diff` — see unstaged changes
3. `git diff --cached` — see staged changes
4. `git log --oneline -5` — see recent commit message style

## Step 2: Draft the Commit Message

Analyze all changes (both staged and unstaged) and draft a message:

- Summarize the nature of the changes (new feature, bug fix, refactor, docs, etc.)
- Use the repository's existing commit message convention (check recent commits)
- If no convention exists, use: `<type>: <summary>` where type is feat/fix/refactor/docs/chore/test
- Keep the first line under 72 characters
- Focus on the "why" rather than the "what"
- Do NOT commit files that likely contain secrets (.env, credentials, API keys)

$ARGUMENTS

## Step 3: Stage and Commit

1. Add relevant files to staging (prefer specific files over `git add -A`)
2. Create the commit
3. Run `git status` after to verify success

## Important Rules

- If there are no changes to commit, say so — do not create an empty commit
- Never use `--no-verify` or skip hooks unless the user explicitly asks
- Never amend existing commits unless the user explicitly asks
- If a pre-commit hook fails, fix the issue and create a NEW commit (do not amend)
- Do not push unless the user explicitly asks
