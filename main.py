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
GITHUB_TOKEN = None  # set your token string here or leave None

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

def get_github_members(github_url, token=None):
    """Get public members via GitHub API (list of dicts)."""
    org = get_org_from_github_url(github_url)
    if not org:
        return []
    headers = {"Authorization": f"token {token}"} if token else {}
    try:
        r = requests.get(f"https://api.github.com/orgs/{org}/members", headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        members = []
        for item in r.json():
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
                    "twitter": twitter_url,   # <-- clickable link
                    "email": ud.get("email")
                })
        return members
    except Exception:
        return []


def extract_githubs_from_site(site_url):
    """
    Fetch site_url HTML and return dict of {org:count} for github orgs found.
    Uses regex only (no new libs). Returns normalized org-level URLs.
    """
    try:
        # normalize site_url
        if site_url.startswith("//"):
            site_url = "https:" + site_url
        if not site_url.startswith("http"):
            site_url = "https://" + site_url.lstrip("/")
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CompanyInfoBot/1.0)"}
        r = requests.get(site_url, headers=headers, timeout=8)
        if r.status_code != 200:
            return {}
        html = r.text.lower()
        # find github.com/org or github.com/org/repo patterns
        matches = re.findall(r'github\.com/([a-z0-9_.-]+)(?:/[a-z0-9_.-]+)?', html, flags=re.I)
        counts = {}
        for org in matches:
            org = org.strip().lower()
            if not org:
                continue
            counts[org] = counts.get(org, 0) + 1
        return counts
    except Exception:
        return {}

def choose_best_org_from_site(counts_dict, company_name):
    """
    Given a dict {org:count} and the company name, pick the best org.
    Heuristics: occurrence count, token overlap with company_name.
    """
    if not counts_dict:
        return None
    company = (company_name or "").lower()
    tokens = re.findall(r'[a-z0-9]+', company)
    best = None
    best_score = -1
    for org, count in counts_dict.items():
        score = count  # base score is number of occurrences
        # exact or substring match bonus
        if org in company or any(tok and tok in org for tok in tokens):
            score += 5
        # longer org names that include multiple tokens get small bonus
        overlap = sum(1 for tok in tokens if tok and tok in org)
        score += overlap * 2
        if score > best_score:
            best_score = score
            best = org
    if best:
        return f"https://github.com/{best}"
    return None

# --------------------------
# Streamlit UI
# --------------------------
st.title("🔍 Web3 Company Info Finder — improved GitHub selection")

company_name = st.text_input("Enter Web3/Blockchain project name or website:")

if st.button("Search"):
    if not company_name or not company_name.strip():
        st.warning("Please enter a project name or website.")
    else:
        with st.spinner("Searching..."):
            # targeted prompts
            queries = {
                "Website": f"Find the official main website for the Web3/blockchain project {company_name}. Return only the link.",
                "General Contacts": f"Find official contact details for {company_name} (Web3 project). Include emails and contact pages.",
                "Twitter": f"Find the official Twitter (X) account for {company_name}. Return only the link(s).",
                "LinkedIn": f"Find the official LinkedIn page for {company_name}. Return only the link(s).",
                "GitHub": f"Find the official GitHub organization or repositories for {company_name}. Return only the link(s)."
            }

            # ask Exa for results
            report_sections = {}
            for sec, q in queries.items():
                try:
                    report_sections[sec] = query_exa(q)
                except Exception as e:
                    report_sections[sec] = ""
                    st.error(f"LLM query failed for {sec}: {e}")

            # extract website and LLM github suggestion
            website_links = extract_links(report_sections.get("Website", ""))
            website_url = website_links[0] if website_links else None

            llm_githubs = extract_links(report_sections.get("GitHub", ""), ["github.com"])
            linkedin_links = extract_links(report_sections.get("LinkedIn", ""), ["linkedin.com"])

            chosen_githubs = []

            # 1) If website present: scan it for github orgs and pick best
            if website_url:
                site_counts = extract_githubs_from_site(website_url)  # dict org->count
                best_site_org_url = choose_best_org_from_site(site_counts, company_name)
                if best_site_org_url and get_org_from_github_url(best_site_org_url) and github_org_exists(get_org_from_github_url(best_site_org_url), token=GITHUB_TOKEN):
                    chosen_githubs = [best_site_org_url]
                    source = "website"
                else:
                    # try to validate any site candidates (if any) that exist on GitHub
                    valid_site_orgs = []
                    for org in site_counts.keys():
                        if github_org_exists(org, token=GITHUB_TOKEN):
                            valid_site_orgs.append(f"https://github.com/{org}")
                    if valid_site_orgs:
                        chosen_githubs = valid_site_orgs
                        source = "website"

            # 2) If no chosen from site, verify LLM suggestions and keep valid ones
            if not chosen_githubs and llm_githubs:
                valid_llm = []
                for g in llm_githubs:
                    org = get_org_from_github_url(g)
                    if org and github_org_exists(org, token=GITHUB_TOKEN):
                        valid_llm.append(f"https://github.com/{org}")
                if valid_llm:
                    chosen_githubs = valid_llm
                    source = "llm"

            # 3) Strict LLM query if still nothing
            if not chosen_githubs:
                try:
                    strict = query_exa(f"Return ONLY the official GitHub organization URL for the Web3 project {company_name}, nothing else.")
                    strict_list = extract_links(strict, ["github.com"])
                    for g in strict_list:
                        org = get_org_from_github_url(g)
                        if org and github_org_exists(org, token=GITHUB_TOKEN):
                            chosen_githubs.append(f"https://github.com/{org}")
                    if chosen_githubs:
                        source = "strict-llm"
                except Exception:
                    pass

            # 4) Last resort: use LLM-provided githubs unverified (rare), but try to normalize to org-level
            if not chosen_githubs and llm_githubs:
                normalized = []
                for g in llm_githubs:
                    org = get_org_from_github_url(g)
                    if org:
                        normalized.append(f"https://github.com/{org}")
                chosen_githubs = list(dict.fromkeys(normalized))  # dedupe
                if chosen_githubs:
                    source = "llm-unverified"

             # Display aggregated results
            st.subheader("📄 All Links Found")
            for sec in ["Website", "General Contacts", "Twitter", "LinkedIn", "GitHub"]:
                st.write(f"**{sec}:**\n{report_sections.get(sec, 'N/A')}")

            if linkedin_links:
                st.subheader("🔗 LinkedIn Links")
                for l in linkedin_links:
                    st.write(l)

            if chosen_githubs:
                st.subheader(f"🐙 GitHub Links & Members (source: {source})")
                for link in chosen_githubs:
                    st.write(f"**{link}**")
                    members = get_github_members(link, token=GITHUB_TOKEN)
                    if members:
                        # make Twitter clickable
                        for m in members:
                            if m["twitter"]:
                                handle = m["twitter"].lstrip("@")  # clean up
                                m["twitter"] = f"[{m['twitter']}](https://twitter.com/{handle})"
                        st.write("Members (public via GitHub API):")
                        st.table(members)
                    else:
                        st.info("⚠️ No public members found via GitHub API for this org. (GitHub only exposes public members.)")
            else:
                st.info("No GitHub org found for this project (website + LLM checks).")
