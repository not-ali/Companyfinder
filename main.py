import os
import re
import requests
import pandas as pd
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

# Initialize OpenAI/Exa client
client = OpenAI(base_url="https://api.exa.ai", api_key=api_key)

# Optional GitHub token
GITHUB_TOKEN = None  # e.g., "ghp_XXXXXXXXXXXXXXXXXXXX"

# --------------------------
# Utility Functions
# --------------------------
def extract_links(text, domains=None):
    seen = set()
    ordered = []

    def add(url):
        url = url.strip().rstrip(").,]>'\"")
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)

    for match in re.finditer(r'\[([^\]]*?)\]\((https?://[^\s)]+)\)', text):
        add(match.group(2))
    for match in re.finditer(r'https?://[^\s\)\]\}\,\'"]+', text):
        add(match.group(0))

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

def get_org_name_from_github_url(url):
    parts = url.rstrip("/").split("/")
    if "github.com" in parts:
        idx = parts.index("github.com")
        return parts[idx + 1]  # org name
    return None

def get_github_members(github_url, token=None):
    org_name = get_org_name_from_github_url(github_url)
    if not org_name:
        st.warning(f"❌ Could not extract org name from URL: {github_url}")
        return []

    api_url = f"https://api.github.com/orgs/{org_name}/members"
    headers = {"Authorization": f"token {token}"} if token else {}

    try:
        response = requests.get(api_url, headers=headers)
        if response.status_code != 200:
            st.warning(f"GitHub API returned {response.status_code} for {org_name}")
            return []

        members = []
        for m in response.json():
            login = m["login"]
            user_res = requests.get(f"https://api.github.com/users/{login}", headers=headers)
            if user_res.status_code == 200:
                data = user_res.json()
                members.append({
                    "login": login,
                    "name": data.get("name"),
                    "url": data.get("html_url"),
                    "twitter": data.get("twitter_username"),
                    "email": data.get("email")
                })
        return members
    except Exception as e:
        st.error(f"⚠️ Error fetching members: {e}")
        return []

# --------------------------
# Streamlit UI
# --------------------------
st.title("🔍 Company Info Finder")

company_name = st.text_input("Enter company name or website:")

if st.button("Search"):
    if not company_name.strip():
        st.warning("Please enter a company name or website.")
    else:
        with st.spinner("Searching..."):
            queries = {
                "Website": f"Find the official main website for {company_name}. Return only the link.",
                "General Contacts": f"Find official contact details for {company_name}. Include: emails, phone numbers, contact pages.",
                "Twitter": f"Find the official Twitter account for {company_name}. Return only the link(s).",
                "LinkedIn": f"Find the official LinkedIn page for {company_name}. Return only the link(s).",
                "GitHub": f"Find the official GitHub organization or repositories for {company_name}. Return only the link(s)."
            }

            report_sections = {sec: query_exa(q) for sec, q in queries.items()}

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
                for link in github_links:
                    st.write(f"**{link}**")
                    members = get_github_members(link, token=GITHUB_TOKEN)
                    if members:
                        df = pd.DataFrame(members)
                        df = df[['login', 'name', 'url', 'twitter', 'email']]
                        st.dataframe(df)
                    else:
                        st.info("No public members found or access restricted.")
