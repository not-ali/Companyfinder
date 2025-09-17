import os
import re
import requests
from openai import OpenAI
import streamlit as st

# --------------------------
# API Key Handling
# --------------------------
api_key = None
try:
    api_key = st.secrets["EXA_API_KEY"]
except Exception:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.getenv("EXA_API_KEY")
    except ImportError:
        st.error("⚠️ python-dotenv not installed, and no Streamlit secret found.")

if not api_key:
    st.error("❌ No API key found. Please set EXA_API_KEY in .env or Streamlit secrets.")
    st.stop()

# Initialize Exa/OpenAI client
client = OpenAI(base_url="https://api.exa.ai", api_key=api_key)

# Optional GitHub token (improves rate limits)
GITHUB_TOKEN = None
try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
except Exception:
    pass

# --------------------------
# Utility Functions
# --------------------------
def extract_links(text, domains=None):
    """Extract unique URLs from text (markdown links and bare URLs)."""
    seen = set()
    ordered = []

    def add(url):
        u = url.strip().rstrip(").,]>'\"")
        if u and u not in seen:
            seen.add(u)
            ordered.append(u)

    for m in re.finditer(r'\[([^\]]*?)\]\((https?://[^\s)]+)\)', text):
        add(m.group(2))
    for m in re.finditer(r'https?://[^\s\)\]\}\,\'"]+', text):
        add(m.group(0))

    if domains:
        domains = [d.lower() for d in domains]
        return [u for u in ordered if any(d in u.lower() for d in domains)]
    return ordered

def query_exa(prompt):
    completion = client.chat.completions.create(
        model="exa",
        messages=[{"role": "user", "content": prompt}],
    )
    return completion.choices[0].message.content.strip()

def get_org_from_github_url(url):
    """Return first path segment after github.com (org name) or None."""
    m = re.search(r'github\.com/([A-Za-z0-9_.-]+)', url, flags=re.I)
    return m.group(1) if m else None

def github_org_exists(org, token=None):
    """Return True if https://api.github.com/orgs/{org} exists (200)."""
    try:
        headers = {"Authorization": f"token {token}"} if token else {}
        r = requests.get(f"https://api.github.com/orgs/{org}", headers=headers, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

def scrape_people_page(org):
    """
    Optional: simple regex-based scrape of the /orgs/{org}/people page.
    This is best-effort — GitHub HTML can change and scraping may not always work.
    """
    url = f"https://github.com/orgs/{org}/people"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CompanyInfoBot/1.0)"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        html = r.text

        # Primary: look for elements marked as user hovercards (best signal)
        found = set()
        for m in re.finditer(r'data-hovercard-type="user"\s*href="/([^/"]+)"', html, flags=re.I):
            user = m.group(1).strip()
            if user and user.lower() != org.lower():
                found.add(user)

        # Fallback: generic href pattern, then filter common non-user paths
        if not found:
            candidates = re.findall(r'href="/([A-Za-z0-9-]+)"', html)
            bad = {
                org.lower(), "issues", "pulls", "releases", "security", "settings",
                "features", "projects", "collections", "organizations", "marketplace",
                "about", "contact", "blog", "docs", "help", "login", "search"
            }
            for c in candidates:
                c2 = c.strip()
                if c2 and c2.lower() not in bad and len(c2) > 1:
                    found.add(c2)

        people = [{"login": u, "url": f"https://github.com/{u}"} for u in sorted(found)]
        return people
    except Exception:
        return []

def get_github_members(github_url, token=None, allow_scrape=False, show_debug=False):
    """
    Get public members via GitHub API. If empty and allow_scrape True, try scraping /people.
    Returns list of member dicts. Also (optionally) shows debug info in Streamlit.
    """
    org = get_org_from_github_url(github_url)
    if not org:
        if show_debug:
            st.warning("Could not parse org from URL.")
        return []

    headers = {"Authorization": f"token {token}"} if token else {}
    api_url = f"https://api.github.com/orgs/{org}/members"
    try:
        resp = requests.get(api_url, headers=headers, timeout=10)

        # Debug info: show status so you can see why members are missing on deployment
        if show_debug:
            st.write(f"GitHub API GET {api_url} → {resp.status_code} {resp.reason}")

        if resp.status_code == 200:
            data = resp.json()
            members = []
            for item in data:
                login = item.get("login")
                if not login:
                    continue
                ures = requests.get(f"https://api.github.com/users/{login}", headers=headers, timeout=8)
                if ures.status_code == 200:
                    ud = ures.json()
                    twitter = ud.get("twitter_username")
                    twitter_url = f"https://twitter.com/{twitter}" if twitter else None
                    members.append({
                        "login": login,
                        "name": ud.get("name"),
                        "url": ud.get("html_url"),
                        "twitter": twitter_url,
                        "email": ud.get("email")
                    })
            if members:
                return members
            # empty list from API (no public members)
            if show_debug:
                st.info("GitHub API returned an empty list. This usually means the org has no public members or your token lacks permission to view private members.")
        elif resp.status_code in (401, 403):
            # 403 can be rate-limit or forbidden; 401 = bad credentials
            if show_debug:
                st.error(f"GitHub API returned {resp.status_code}. Possible bad token or rate limit. Response headers: {dict(resp.headers)}")
        elif resp.status_code == 404:
            if show_debug:
                st.warning("GitHub org not found (404).")
        else:
            if show_debug:
                st.warning(f"GitHub API returned {resp.status_code}: {resp.text[:400]}")

        # If API gave nothing and scraping allowed, try people page
        if allow_scrape:
            if show_debug:
                st.write("Attempting fallback scrape of the /people page...")
            scraped = scrape_people_page(org)
            if scraped:
                # scraped is list of dicts with login/url
                # Optionally we could try to fetch detailed info per user if token available—keep it simple
                return scraped
            else:
                if show_debug:
                    st.write("Scrape returned no users.")
        return []
    except Exception as e:
        if show_debug:
            st.error(f"Error while contacting GitHub API: {e}")
        return []

# --------------------------
# Streamlit UI
# --------------------------
st.title("🔍Company Info Finder")

company_name = st.text_input("Enter Web3/Blockchain project name or website:")

# opt-in checkbox to allow fallback scraping of /people (only if API empty)
allow_scrape = st.checkbox("If API shows no members, attempt to extract people from GitHub's /people page (uses regex)", value=False)

# small toggle to show debug details about GitHub API responses
show_debug = st.checkbox("Show GitHub API debug info (status codes & headers)", value=False)

if st.button("Search"):
    if not company_name or not company_name.strip():
        st.warning("Please enter a project name or website.")
    else:
        with st.spinner("Searching..."):
            # queries (same as before)
            queries = {
                "Website": f"Find the official main website for {company_name}. Return only the link.",
                "General Contacts": f"Find official contact details for {company_name}. Include emails and contact pages.",
                "Twitter": f"Find the official Twitter account for {company_name}. Return only the link(s).",
                "LinkedIn": f"Find the official LinkedIn page for {company_name}. Return only the link(s).",
                "GitHub": f"Find the official GitHub organization or repositories for {company_name}. Return only the link(s)."
            }

            report_sections = {}
            for sec, q in queries.items():
                try:
                    report_sections[sec] = query_exa(q)
                except Exception as e:
                    report_sections[sec] = ""
                    st.error(f"LLM query failed for {sec}: {e}")

            # Extract GitHub and LinkedIn links
            linkedin_links = extract_links(report_sections.get("LinkedIn", ""), ["linkedin.com"])
            github_links = extract_links(report_sections.get("GitHub", ""), ["github.com"])

            st.subheader("📄 All Links Found")
            for sec in ["Website", "General Contacts", "Twitter", "LinkedIn", "GitHub"]:
                st.write(f"**{sec}:**\n{report_sections.get(sec, 'N/A')}")

            if linkedin_links:
                st.subheader("🔗 LinkedIn Links")
                for link in linkedin_links:
                    st.write(link)

            if github_links:
                st.subheader("🐙 GitHub Links & Members")
                # show whether token is present (don't show token itself)
                st.write("Using GitHub token:" , bool(GITHUB_TOKEN))
                for link in github_links:
                    st.write(f"**{link}**")
                    members = get_github_members(link, token=GITHUB_TOKEN, allow_scrape=allow_scrape, show_debug=show_debug)
                    if members:
                        # Render members: clickable twitter + profile
                        for m in members:
                            parts = []
                            parts.append(f"**[{m.get('login')}]({m.get('url')})**")
                            parts.append(m.get("name") or "N/A")
                            if m.get("twitter"):
                                parts.append(f"[Twitter]({m.get('twitter')})")
                            else:
                                parts.append("Twitter: N/A")
                            parts.append(f"Email: {m.get('email') or 'N/A'}")
                            st.markdown(" — ".join(parts))
                    else:
                        st.info("No public members found via GitHub API for this org. (GitHub only exposes public members.)")
            else:
                st.info("No GitHub links found in LLM output.")
