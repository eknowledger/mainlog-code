"""Fetch a repository's most recently created merged PRs, with what split_cost.py needs, as JSON lines.

    python3 fetch_prs.py astral-sh/ruff 1000 > data/astral-sh_ruff.jsonl

Uses the GitHub CLI's GraphQL access (`gh auth login` first). For each PR: size, author, creation and merge times,
the last "ready for review" event, the number of review threads, and every review with its author. Reviews come 100
at a time, and a PR with more is completed with a query of its own, so the first outside review is never missed
because it came late.

The post's numbers come from the snapshot in data/, fetched on 2026-10-11, so they don't move when you rerun this.
That snapshot was first fetched with only each PR's first 10 reviews; `complete_reviews.py` filled in the rest for the
100 PRs that had 10, with the `all_reviews` function below.
"""

import json
import subprocess
import sys

QUERY = """query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){
 pullRequests(states:MERGED,first:50,after:$cursor,orderBy:{field:CREATED_AT,direction:DESC}){
  pageInfo{hasNextPage endCursor}
  nodes{number createdAt mergedAt additions deletions changedFiles author{login __typename}
   timelineItems(itemTypes:[READY_FOR_REVIEW_EVENT],last:1){nodes{... on ReadyForReviewEvent{createdAt}}}
   reviewThreads{totalCount}
   reviews(first:100){pageInfo{hasNextPage} nodes{submittedAt state author{login __typename}}}}}}}"""

REVIEWS = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){
 pullRequest(number:$number){reviews(first:100,after:$cursor){pageInfo{hasNextPage endCursor}
  nodes{submittedAt state author{login __typename}}}}}}"""


def gh(query: str, **variables) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            args += ["-F", f"{key}={value}"]
    return json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)["data"]


def all_reviews(owner: str, name: str, number: int) -> list[dict]:
    """Every review of one PR, following pages."""
    out, cursor = [], None
    while True:
        page = gh(REVIEWS, owner=owner, name=name, number=number, cursor=cursor)["repository"]["pullRequest"]["reviews"]
        out += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def main() -> None:
    repo, wanted = sys.argv[1], int(sys.argv[2])
    owner, name = repo.split("/")
    cursor, got = None, 0
    while got < wanted:
        prs = gh(QUERY, owner=owner, name=name, cursor=cursor)["repository"]["pullRequests"]
        for p in prs["nodes"][: wanted - got]:
            if p["reviews"].pop("pageInfo")["hasNextPage"]:
                p["reviews"]["nodes"] = all_reviews(owner, name, p["number"])
            print(json.dumps(p))
            got += 1
        if not prs["pageInfo"]["hasNextPage"]:
            break
        cursor = prs["pageInfo"]["endCursor"]
    print(f"{repo}: {got} PRs", file=sys.stderr)


if __name__ == "__main__":
    main()
