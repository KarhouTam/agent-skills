---
name: gh-github-cli
description: Use gh (GitHub CLI) to fetch and process GitHub issues, pull requests, repositories, and other GitHub resources. Use when the user provides a GitHub URL (https://github.com/*), asks about GitHub issues/PRs, or mentions gh commands.
---

# GitHub CLI (gh) Skill

Process GitHub-related requests using the `gh` CLI tool.

## Quick Start

```bash
# View issue
gh issue view 12345 --repo owner/repo

# View PR
gh pr view 67890 --repo owner/repo

# View PR with details
gh pr view 67890 --repo owner/repo --json title,body,author,state,additions,deletions
```

## Instructions

### 1. Parse GitHub URLs

Extract components from GitHub URLs:

| URL Pattern | Components |
|------------|------------|
| `https://github.com/owner/repo` | owner, repo |
| `https://github.com/owner/repo/issues/123` | owner, repo, issue=123 |
| `https://github.com/owner/repo/pull/456` | owner, repo, pr=456 |
| `https://github.com/owner/repo/blob/main/path/file.py` | owner, repo, branch, file path |
| `https://github.com/owner/repo/tree/main/path` | owner, repo, branch, directory |

Parse with:
```bash
# Extract from URL
echo "https://github.com/pytorch/pytorch/issues/12345" | sed -E 's|https://github.com/([^/]+)/([^/]+)/(issues\|pull)/([0-9]+)|\1 \2 \3 \4|'
```

### 2. Fetch Issue Information

```bash
# Basic view
gh issue view <number> --repo <owner>/<repo>

# JSON output for parsing
gh issue view <number> --repo <owner>/<repo> --json title,body,author,state,labels,comments

# With comments
gh issue view <number> --repo <owner>/<repo> --comments
```

### 3. Fetch Pull Request Information

```bash
# Basic view
gh pr view <number> --repo <owner>/<repo>

# Detailed JSON
gh pr view <number> --repo <owner>/<repo> --json title,body,author,state,additions,deletions,files,commits,reviews

# View diff
gh pr diff <number> --repo <owner>/<repo>

# View checks
gh pr checks <number> --repo <owner>/<repo>

# View merge status
gh pr view <number> --repo <owner>/<repo> --json mergeable,mergeStateStatus
```

### 4. Fetch Repository Information

```bash
# Repo info
gh repo view <owner>/<repo>

# Repo topics/description
gh repo view <owner>/<repo> --json name,description,topics,stargazerCount

# List issues
gh issue list --repo <owner>/<repo> --state open --limit 20

# List PRs
gh pr list --repo <owner>/<repo> --state open --limit 20
```

### 5. Handle API Rate Limits

```bash
# Check rate limit
gh api rate_limit

# Use --paginate for large result sets
gh issue list --repo <owner>/<repo> --state all --paginate
```

### 6. Advanced API Access

```bash
# Direct API call
gh api repos/<owner>/<repo>/issues/<number>

# GraphQL query
gh api graphql -f query='
  query($owner: String!, $repo: String!, $number: Int!) {
    repository(owner: $owner, name: $repo) {
      issue(number: $number) {
        title
        body
        author { login }
      }
    }
  }
' -f owner=<owner> -f repo=<repo> -F number=<number>
```

## Common Workflows

### Triage an Issue

1. Parse URL to get owner/repo/number
2. Fetch issue details:
   ```bash
   gh issue view <number> --repo <owner>/<repo> --json title,body,author,state,labels,comments
   ```
3. Analyze and provide summary

### Review a PR

1. Parse URL to get owner/repo/number
2. Fetch PR details:
   ```bash
   gh pr view <number> --repo <owner>/<repo> --json title,body,author,state,files,additions,deletions
   ```
3. Optionally fetch diff:
   ```bash
   gh pr diff <number> --repo <owner>/<repo>
   ```
4. Review and provide feedback

### Check CI Status

```bash
gh pr checks <number> --repo <owner>/<repo> --watch
```

## Best Practices

- Use `--json` for structured output when processing data programmatically
- Use `--jq` to filter JSON output: `gh issue view 123 --json title,body | jq '.title'`
- Default to `--repo <owner>/<repo>` for clarity in multi-repo contexts
- Check `gh auth status` if commands fail with auth errors
- Use `gh api` for endpoints not covered by convenience commands

## Error Handling

```bash
# Check if gh is installed
if ! command -v gh &> /dev/null; then
    echo "gh CLI not installed. Install from: https://cli.github.com/"
    exit 1
fi

# Check authentication
gh auth status

# Handle missing resources
gh issue view 999999 --repo owner/repo 2>&1 | grep -q "not found" && echo "Issue not found"
```

## Reference

For complete command reference:
```bash
gh help
gh issue --help
gh pr --help
gh api --help
```