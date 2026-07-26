from activity_frames.entities import parse_url


def test_linkedin_profile():
    r = parse_url("https://www.linkedin.com/in/jane-doe/")
    assert (r.kind, r.entity, r.domain) == ("profile", "jane-doe", "linkedin.com")


def test_linkedin_search_with_keywords():
    r = parse_url(
        "https://www.linkedin.com/search/results/people/?keywords=cto%20berlin"
    )
    assert r.kind == "people_search"
    assert r.entity == "cto berlin"


def test_linkedin_company_and_feed():
    assert parse_url("https://www.linkedin.com/company/acme/").kind == "company"
    assert parse_url("https://www.linkedin.com/feed/").kind == "feed"


def test_github_pr_issue_repo():
    pr = parse_url("https://github.com/acme/api/pull/7")
    assert (pr.kind, pr.entity) == ("pull_request", "acme/api#7")
    issue = parse_url("https://github.com/acme/api/issues/12")
    assert (issue.kind, issue.entity) == ("issue", "acme/api#12")
    repo = parse_url("https://github.com/acme/api")
    assert (repo.kind, repo.entity) == ("repo", "acme/api")


def test_google_search():
    r = parse_url("https://www.google.com/search?q=swift+sqlite+wrapper")
    assert r.kind == "search"
    assert r.entity == "swift sqlite wrapper"


def test_youtube_video_and_search():
    v = parse_url("https://www.youtube.com/watch?v=abc123")
    assert (v.kind, v.entity) == ("video", "abc123")
    s = parse_url("https://www.youtube.com/results?search_query=mcp+tutorial")
    assert (s.kind, s.entity) == ("search", "mcp tutorial")


def test_x_profile_vs_system_paths():
    assert parse_url("https://x.com/someuser").kind == "profile"
    assert parse_url("https://x.com/notifications").kind == "notifications"
    assert parse_url("https://x.com/i/lists/123").kind == "page"
    assert parse_url("https://x.com/user/status/999").kind == "post"


def test_generic_fallback_and_search_param():
    r = parse_url("https://example.com/docs/getting-started")
    assert (r.kind, r.domain, r.entity) == ("page", "example.com", "docs")
    s = parse_url("https://anysite.io/find?q=hello%20world")
    assert (s.kind, s.entity) == ("search", "hello world")


def test_garbage_urls_never_raise():
    assert parse_url("").kind == "page"
    assert parse_url("not a url").kind == "page"
    assert parse_url("http://").kind == "page"


def test_entity_whitespace_normalized():
    r = parse_url(
        "https://www.linkedin.com/search/results/people/?keywords=jamie%20%20founder"
    )
    assert r.entity == "jamie founder"


def test_linkedin_extended_paths():
    assert parse_url("https://www.linkedin.com/mynetwork/grow/").kind == "network"
    assert parse_url("https://www.linkedin.com/notifications/").kind == "notifications"
    assert parse_url("https://www.linkedin.com/uas/login").kind == "sign_in"
    p = parse_url("https://www.linkedin.com/posts/acme-launches-beta-abc123")
    assert p.kind == "post"


def test_x_extended_paths():
    assert parse_url("https://x.com/i/chat/123-456").kind == "messages"
    assert parse_url("https://x.com/i/flow/login").kind == "sign_in"
    assert parse_url("https://x.com/settings/profile").kind == "page"


def test_instagram():
    assert parse_url("https://www.instagram.com/p/ABC123/").kind == "post"
    reel = parse_url("https://www.instagram.com/reel/XYZ789/")
    assert (reel.kind, reel.entity) == ("reel", "XYZ789")
    assert parse_url("https://www.instagram.com/stories/someone/").kind == "story"
    assert parse_url("https://www.instagram.com/explore/").kind == "explore"
    assert parse_url("https://www.instagram.com/direct/inbox/").kind == "messages"
    assert parse_url("https://www.instagram.com/janedoe/").kind == "profile"


def test_reddit():
    sub = parse_url("https://www.reddit.com/r/AskProgramming/")
    assert (sub.kind, sub.entity) == ("subreddit", "r/AskProgramming")
    post = parse_url("https://www.reddit.com/r/AskProgramming/comments/abc/some_title/")
    assert post.kind == "post"
    u = parse_url("https://www.reddit.com/user/some_user/")
    assert (u.kind, u.entity) == ("profile", "u/some_user")


def test_google_maps_and_products():
    assert parse_url("https://www.google.com/maps/place/Central+Park").kind == "map_place"
    assert parse_url("https://www.google.com/maps/dir/a/b").kind == "map_directions"
    assert parse_url("https://meet.google.com/abc-defg-hij").kind == "meeting"
    assert parse_url("https://calendar.google.com/calendar/u/0/r").kind == "calendar"


def test_events_and_dashboards():
    assert parse_url("https://luma.com/abc123").kind == "event"
    assert parse_url("https://lu.ma/xyz").kind == "event"
    assert parse_url("https://partiful.com/e/abcDEF").kind == "event"
    pp = parse_url("https://www.producthunt.com/posts/some-cool-tool")
    assert (pp.kind, pp.entity) == ("product", "some cool tool")
    ph = parse_url("https://www.producthunt.com/@some_maker")
    assert (ph.kind, ph.entity) == ("profile", "some maker")
    assert parse_url("https://vercel.com/team/project").kind == "project"
    sb = parse_url("https://supabase.com/dashboard/project/abcref")
    assert (sb.kind, sb.entity) == ("project", "abcref")
    assert parse_url("https://dashboard.stripe.com/payments").kind == "dashboard"


def test_discord_and_notion():
    ch = parse_url("https://discord.com/channels/123/456")
    assert (ch.kind, ch.entity) == ("channel", "123")
    assert parse_url("https://discord.com/channels/@me").kind == "messages"
    n = parse_url("https://app.notion.com/workspace/My-Project-Notes-abc123")
    assert n.kind == "doc"
    assert n.entity == "My Project Notes"


def test_slack_app_conversation_and_workspace():
    conversation = parse_url("https://app.slack.com/client/T123ABC456/C123ABC456")
    assert (conversation.kind, conversation.entity, conversation.domain) == (
        "messaging", "T123ABC456/C123ABC456", "app.slack.com"
    )
    workspace = parse_url("https://app.slack.com/client/T123ABC456")
    assert (workspace.kind, workspace.entity) == ("messaging", "T123ABC456")
    root = parse_url("https://app.slack.com/client")
    assert (root.kind, root.entity) == ("messaging", None)


def test_slack_workspace_permalink():
    channel = parse_url("https://acme.slack.com/archives/C123ABC456")
    assert (channel.kind, channel.entity) == ("messaging", "acme/C123ABC456")
    message = parse_url(
        "https://acme.slack.com/archives/C123ABC456/p135854651500008"
    )
    assert (message.kind, message.entity) == (
        "messaging", "acme/C123ABC456/p135854651500008"
    )


def test_slack_app_redirect():
    linked = parse_url(
        "https://slack.com/app_redirect?team=T123ABC456&channel=C123ABC456"
    )
    assert (linked.kind, linked.entity) == (
        "messaging", "T123ABC456/C123ABC456"
    )
    named = parse_url("https://slack.com/app_redirect?channel=release-notes")
    assert (named.kind, named.entity) == ("messaging", "release-notes")


def test_slack_home_workspace_signin_and_unknown():
    assert parse_url("https://slack.com/").kind == "home"
    workspace = parse_url("https://acme.slack.com/")
    assert (workspace.kind, workspace.entity) == ("messaging", "acme")
    assert parse_url("https://slack.com/signin").kind == "sign_in"
    unknown = parse_url("https://slack.com/features")
    assert (unknown.kind, unknown.entity) == ("page", "features")


def test_heuristic_layer_types_unparsed_sites():
    # No bespoke parser for these hosts, but heuristics still type them.
    assert parse_url("https://accounts.google.com/signin/v2").kind == "sign_in"
    assert parse_url("https://app.attio.com/workspace/records").kind == "dashboard"
    assert parse_url("https://us.posthog.com/dashboard").kind == "dashboard"
    assert parse_url("https://mail.proton.me/u/0/inbox").kind == "email"
    assert parse_url("https://some-startup.com/login").kind == "sign_in"
    assert parse_url("https://zoom.us/j/123456").kind == "meeting"


def test_heuristic_does_not_override_real_parsers():
    # app.notion.com starts with "app." but must stay a notion doc, not a dashboard.
    assert parse_url("https://app.notion.com/ws/Page-abc").kind == "doc"


def test_linear_issue():
    r = parse_url("https://linear.app/acme/issue/ENG-123/fix-null-pointer")
    assert r.kind == "issue"
    assert r.entity == "ENG-123"
    assert r.domain == "linear.app"


def test_linear_project():
    r = parse_url("https://linear.app/acme/project/mobile-app-rewrite-abc123")
    assert r.kind == "project"
    assert r.entity == "mobile app rewrite abc123"


def test_linear_workspace_dashboard():
    r = parse_url("https://linear.app/acme")
    assert r.kind == "dashboard"
    assert r.entity == "acme"


def test_linear_cycles_and_roadmap():
    assert parse_url("https://linear.app/acme/cycles").kind == "cycles"
    assert parse_url("https://linear.app/acme/roadmap").kind == "roadmap"


def test_linear_inbox_and_my_issues():
    assert parse_url("https://linear.app/acme/inbox").kind == "notifications"
    assert parse_url("https://linear.app/acme/my-issues").kind == "my_issues"


def test_linear_views():
    assert parse_url("https://linear.app/acme/views").kind == "views"


def test_linear_signin_and_join():
    assert parse_url("https://linear.app/signin").kind == "sign_in"
    assert parse_url("https://linear.app/join/abc123token").kind == "sign_in"


def test_linear_unknown_subpath_falls_through_to_generic():
    # An unrecognised workspace sub-path (e.g. /acme/team) should fall
    # through to the generic page fallback, not raise.
    r = parse_url("https://linear.app/acme/team")
    assert r.kind == "page"
    assert r.domain == "linear.app"


def test_gitlab_issue_and_merge_request():
    i = parse_url("https://gitlab.com/acme/api/-/issues/42")
    assert (i.kind, i.entity, i.domain) == ("issue", "acme/api#42", "gitlab.com")
    mr = parse_url("https://gitlab.com/acme/api/-/merge_requests/7")
    assert (mr.kind, mr.entity) == ("merge_request", "acme/api!7")


def test_gitlab_repo_and_subgroups():
    r = parse_url("https://gitlab.com/acme/api")
    assert (r.kind, r.entity) == ("repo", "acme/api")
    sub = parse_url("https://gitlab.com/acme/platform/api/-/issues/3")
    assert (sub.kind, sub.entity) == ("issue", "acme/platform/api#3")


def test_gitlab_code_commits_pipelines():
    c = parse_url("https://gitlab.com/acme/api/-/blob/main/src/app.py")
    assert (c.kind, c.entity) == ("code", "acme/api")
    assert parse_url("https://gitlab.com/acme/api/-/tree/main/src").kind == "code"
    cm = parse_url("https://gitlab.com/acme/api/-/commits/main")
    assert (cm.kind, cm.entity) == ("commits", "acme/api")
    assert parse_url("https://gitlab.com/acme/api/-/pipelines/12345").kind == "pipelines"


def test_gitlab_unknown_project_resource_stays_repo():
    # An unrecognised /-/ resource (e.g. releases) still types as the project.
    r = parse_url("https://gitlab.com/acme/api/-/releases")
    assert (r.kind, r.entity) == ("repo", "acme/api")
    # Unnumbered issue list is the project's page, not a specific issue.
    assert parse_url("https://gitlab.com/acme/api/-/issues").kind == "repo"


def test_gitlab_group_search_dashboard_explore():
    g = parse_url("https://gitlab.com/groups/acme/platform")
    assert (g.kind, g.entity) == ("group", "acme/platform")
    s = parse_url("https://gitlab.com/search?search=rate%20limiter")
    assert (s.kind, s.entity) == ("search", "rate limiter")
    assert parse_url("https://gitlab.com/dashboard/todos").kind == "dashboard"
    assert parse_url("https://gitlab.com/explore/projects/trending").kind == "explore"


def test_gitlab_sign_in_and_home():
    assert parse_url("https://gitlab.com/users/sign_in").kind == "sign_in"
    assert parse_url("https://gitlab.com/users/sign_up").kind == "sign_in"
    assert parse_url("https://gitlab.com/").kind == "home"


def test_gitlab_single_segment_falls_through():
    # A single segment may be a user or a group; leave it to the generic
    # fallback instead of guessing.
    r = parse_url("https://gitlab.com/some-user")
    assert (r.kind, r.domain, r.entity) == ("page", "gitlab.com", "some-user")

def test_leetcode():
    problem = parse_url("https://leetcode.com/problems/two-sum/")
    assert (problem.kind, problem.entity) == ("problem", "two sum")

    contest = parse_url("https://leetcode.com/contest/")
    assert contest.kind == "contest"

    # Bare problems section should fall through instead of raising.
    bare = parse_url("https://leetcode.com/problems")
    assert (bare.kind, bare.domain, bare.entity) == (
        "page",
        "leetcode.com",
        "problems",
    )

def test_crunchbase_company_and_profile():
    org = parse_url("https://www.crunchbase.com/organization/acme")
    assert (org.kind, org.domain, org.entity) == ("company", "crunchbase.com", "acme")
    person = parse_url("https://www.crunchbase.com/person/jane-doe")
    assert (person.kind, person.domain, person.entity) == ("profile", "crunchbase.com", "jane doe")


def test_crunchbase_funding_and_discover():
    funding = parse_url("https://www.crunchbase.com/funding_round/acme-seed--abc12345")
    assert (funding.kind, funding.entity) == ("funding_round", "acme seed abc12345")
    search = parse_url("https://www.crunchbase.com/discover/organization.companies")
    assert search.kind == "search"
    assert search.entity == "organization.companies"


def test_atlassian_jira_issue():
    issue = parse_url("https://acme.atlassian.net/browse/ENG-123")
    assert (issue.kind, issue.entity, issue.domain) == (
        "issue", "ENG-123", "acme.atlassian.net"
    )


def test_atlassian_confluence_page():
    titled = parse_url(
        "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345/API+Runbook"
    )
    assert (titled.kind, titled.entity) == ("doc", "API Runbook")
    untitled = parse_url(
        "https://acme.atlassian.net/wiki/spaces/ENG/pages/12345"
    )
    assert (untitled.kind, untitled.entity) == ("doc", "12345")


def test_atlassian_unknown_path_falls_through():
    unknown = parse_url("https://acme.atlassian.net/plugins/servlet/ac")
    assert (unknown.kind, unknown.entity) == ("page", "plugins")
