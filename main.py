import os
import re
import requests
from openai import OpenAI
import streamlit as st
from bs4 import BeautifulSoup

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

client = OpenAI(base_url="https://api.exa.ai", api_key=api_key)

# Optional GitHub token
GITHUB_TOKEN = None
try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
except Exception:
    pass

# --------------------------
# Utility Functions
# --------------------------
def extract_links(text, domains=None):
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
    m = re.search(r'github\.com/([A-Za-z0-9_.-]+)', url, flags=re.I)
    return m.group(1) if m else None


def extract_emails_from_text(text):
    return re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)


def scrape_github_profile_for_contacts(username):
    """Scrape LinkedIn + emails from profile HTML."""
    url = f"https://github.com/{username}"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None, None

        linkedin = None
        email = None
        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "linkedin.com" in href.lower():
                linkedin = href if href.startswith("http") else "https://" + href.lstrip("/")
            if href.lower().startswith("mailto:"):
                email = href.replace("mailto:", "").strip()

        if not email:
            text = soup.get_text(" ")
            emails = extract_emails_from_text(text)
            if emails:
                email = emails[0]

        return linkedin, email
    except Exception:
        return None, None


def get_email_from_events(username, headers):
    """Try to extract email from recent public GitHub events."""
    try:
        r = requests.get(f"https://api.github.com/users/{username}/events/public", headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        events = r.json()
        for e in events:
            if e.get("type") == "PushEvent":
                commits = e.get("payload", {}).get("commits", [])
                for c in commits:
                    email = c.get("author", {}).get("email")
                    if email and "noreply" not in email:
                        return email
        return None
    except Exception:
        return None


def get_github_members(github_url, token=None):
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

            # Get user details
            ures = requests.get(f"https://api.github.com/users/{login}", headers=headers, timeout=8)
            if ures.status_code == 200:
                ud = ures.json()

                twitter = ud.get("twitter_username")
                twitter_url = f"https://x.com/{twitter}" if twitter else None
                blog = ud.get("blog") or None
                email = ud.get("email")  # ✅ Direct from GitHub API
                bio = ud.get("bio") or ""

                # If API didn't return email, check inside bio
                if not email:
                    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", bio)
                    if email_match:
                        email = email_match.group(0)

                # Try LinkedIn in bio/blog
                linkedin_link = None
                if "linkedin.com" in bio.lower():
                    linkedin_match = re.search(r"(https?://[^\s]+linkedin\.com[^\s]*)", bio, re.I)
                    if linkedin_match:
                        linkedin_link = linkedin_match.group(1)
                if not linkedin_link and blog and "linkedin.com" in blog.lower():
                    linkedin_link = blog

                # Final fallback: scrape GitHub profile page
                if not linkedin_link or not email:
                    scraped_linkedin, scraped_email = scrape_github_profile_for_contacts(login)
                    if not linkedin_link and scraped_linkedin:
                        linkedin_link = scraped_linkedin
                    if not email and scraped_email:
                        email = scraped_email

                # Last chance: look inside commit history via events API
                if not email:
                    email_from_events = get_email_from_events(login, headers)
                    if email_from_events:
                        email = email_from_events

                members.append({
                    "login": login,
                    "name": ud.get("name"),
                    "url": ud.get("html_url"),
                    "x": twitter_url,
                    "email": email,
                    "blog": blog if blog and "linkedin.com" not in (blog.lower()) else None,
                    "linkedin": linkedin_link
                })
        return members
    except Exception as e:
        st.error(f"⚠️ GitHub members fetch failed: {e}")
        return []


def extract_githubs_from_site(site_url):
    try:
        if site_url.startswith("//"):
            site_url = "https:" + site_url
        if not site_url.startswith("http"):
            site_url = "https://" + site_url.lstrip("/")
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CompanyInfoBot/1.0)"}
        r = requests.get(site_url, headers=headers, timeout=8)
        if r.status_code != 200:
            return {}
        html = r.text.lower()
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
    if not counts_dict:
        return None
    company = (company_name or "").lower()
    tokens = re.findall(r'[a-z0-9]+', company)
    best = None
    best_score = -1
    for org, count in counts_dict.items():
        score = count
        if org in company or any(tok and tok in org for tok in tokens):
            score += 5
        overlap = sum(1 for tok in tokens if tok and tok in org)
        score += overlap * 2
        if score > best_score:
            best_score = score
            best = org
    if best:
        return f"https://github.com/{best}"
    return None


def filter_github_orgs(urls, token=None):
    """Return only valid GitHub organizations."""
    valid_orgs = []
    headers = {"Authorization": f"token {token}"} if token else {}
    for url in urls:
        org = get_org_from_github_url(url)
        if not org:
            continue
        r = requests.get(f"https://api.github.com/orgs/{org}", headers=headers, timeout=8)
        if r.status_code == 200:
            valid_orgs.append(f"https://github.com/{org}")
    return valid_orgs

# --------------------------
# Streamlit UI
# --------------------------
st.title("🏢 Company Info")

company_name = st.text_input("Enter Web3/Blockchain project name or website:")

if st.button("Search"):
    if not company_name.strip():
        st.warning("Please enter a project name or website.")
    else:
        with st.spinner("🔍 Searching..."):
            queries = {
                "Website": f"Find the official main website for the Web3/blockchain project {company_name}. Return only the link.",
                "LinkedIn": f"Find the official LinkedIn page for {company_name}. Return only the link(s).",
                "GitHub": f"Find the official GitHub organization or repositories for {company_name}. Return only the link(s)."
            }
            
            report_sections = {}
            for sec, q in queries.items():
                try:
                    report_sections[sec] = query_exa(q)
                except Exception:
                    report_sections[sec] = ""

            website_links = extract_links(report_sections.get("Website", ""))
            website_url = website_links[0] if website_links else None

            llm_githubs = extract_links(report_sections.get("GitHub", ""), ["github.com"])
            linkedin_links = extract_links(report_sections.get("LinkedIn", ""), ["linkedin.com"])

            # ---------------- GitHub Link Selection ----------------
            chosen_githubs = set()

            if website_url:
                site_counts = extract_githubs_from_site(website_url)
                best_site_org_url = choose_best_org_from_site(site_counts, company_name)
                if best_site_org_url:
                    chosen_githubs.add(best_site_org_url)
                for org in site_counts.keys():
                    chosen_githubs.add(f"https://github.com/{org}")

            for g in llm_githubs:
                chosen_githubs.add(g)

            # Filter only valid GitHub organizations
            chosen_githubs = filter_github_orgs(list(chosen_githubs), token=GITHUB_TOKEN)

            # ---------------- Output ----------------
            st.subheader("🏢 Company Info")
            st.markdown(f"**Website:** {f'[{website_url}]({website_url})' if website_url else 'None'}")
            st.markdown(f"**LinkedIn:** {f'[{linkedin_links[0]}]({linkedin_links[0]})' if linkedin_links else 'None'}")

            if chosen_githubs:
                github_links_md = " | ".join([f"[{g}]({g})" for g in chosen_githubs])
                st.markdown(f"**GitHub:** {github_links_md}")
            else:
                st.markdown("**GitHub:** None")

            if chosen_githubs:
                st.subheader("👥 GitHub Members:")
                for idx, link in enumerate(chosen_githubs, start=1):
                    members = get_github_members(link, token=GITHUB_TOKEN)
                    if not members:
                        st.markdown(f"**No members found for [{link}]({link})**")
                        continue
                    
                    for j, m in enumerate(members, start=1):
                        st.markdown(f"**{j}. {m['login']} — [{m['url']}]({m['url']})**")
                        st.markdown(f"- Name: {m['name'] or 'N/A'}")
                        st.markdown(f"- LinkedIn: {m['linkedin'] if m['linkedin'] else 'None'}")
                        st.markdown(f"- X (Twitter): {m['x'] if m['x'] else 'None'}")
                        st.markdown(f"- Email: [{m['email']}](mailto:{m['email']})" if m['email'] else "- Email: None")
                        st.markdown(f"- Portfolio/Web: [{m['blog']}]({m['blog']})" if m['blog'] else "- Portfolio/Web: None")
                        st.markdown("---")
