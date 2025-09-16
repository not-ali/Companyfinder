import os
import re
import requests
from rich import print
from openai import OpenAI
import streamlit as st

# --------------------------
# API Key Handling
# --------------------------

api_key = None

# Try Streamlit secrets first (Cloud)
try:
    api_key = st.secrets["EXA_API_KEY"]
except Exception:
    # Fallback to .env (Local)
    try:
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.getenv("EXA_API_KEY")
    except ImportError:
        st.error("⚠️ python-dotenv not installed, and no Streamlit secret found.")

if not api_key:
    st.error("❌ No API key found. Please set EXA_API_KEY in .env (local) or in Streamlit secrets (Cloud).")
    st.stop()

# Initialize client
client = OpenAI(
    base_url="https://api.exa.ai",
    api_key=api_key,
)

# --------------------------
# Helper functions
# --------------------------

def sanitize_response(text: str) -> str:
    """
    Remove lines we don't want to display (e.g., GitHub security/support emails).
    """
    block_patterns = [
        r"Email:\s*For security vulnerabilities.*",
        r"Support:\s*Visit their support site.*",
    ]
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        if any(re.search(pat, line, flags=re.IGNORECASE) for pat in block_patterns):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def extract_unique_urls(text, allow_domains=None):
    seen = set()
    ordered = []

    def add(url):
        url = url.strip()
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        url = url.rstrip(").,]>'\"")
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)

    # markdown links
    for match in re.finditer(r'\[([^\]]*?)\]\((https?://[^\s)]+)\)', text):
        add(match.group(2))

    # bare urls
    for match in re.finditer(r'https?://[^\s\)\]\}\,\'"]+', text):
        add(match.group(0))

    if allow_domains:
        allow_domains = [d.lower() for d in allow_domains]
        return [u for u in ordered if any(d in u.lower() for d in allow_domains)]
    return ordered


def get_github_members(org_url):
    try:
        org_name = org_url.rstrip("/").split("/")[-1]
        api_url = f"https://api.github.com/orgs/{org_name}/members"
        response = requests.get(api_url)

        if response.status_code != 200:
            return []

        members = []
        for m in response.json():
            login = m["login"]
            user_res = requests.get(f"https://api.github.com/users/{login}")
            if user_res.status_code == 200:
                user_data = user_res.json()
                members.append({
                    "login": login,
                    "url": user_data.get("html_url"),
                    "name": user_data.get("name"),
                    "email": user_data.get("email"),
                    "twitter": user_data.get("twitter_username"),
                })
        return members
    except Exception:
        return []

# --------------------------
# Streamlit UI
# --------------------------

st.title("🔍 Company Info Finder")

company_name = st.text_input("Enter company name or website:", "")

if st.button("Search"):
    if not company_name.strip():
        st.warning("Please enter a company name or website.")
    else:
        query = f"""
        Find me any contact details that you can find regarding the following company: {company_name} 
        including but not limited to email, social media, phone numbers, youtube, linkedin, github everything.
        """

        with st.spinner("Searching..."):
            completion = client.chat.completions.create(
                model="exa",
                messages=[{"role": "user", "content": query}]
            )
            response_text = completion.choices[0].message.content
            response_text = sanitize_response(response_text)  # clean unwanted lines

            st.subheader("📄 Links Found")
            st.write(response_text)

            linkedin_links = extract_unique_urls(response_text, allow_domains=["linkedin.com"])
            github_links = extract_unique_urls(response_text, allow_domains=["github.com"])

            if linkedin_links:
                st.subheader("🔗 LinkedIn Links")
                for link in linkedin_links:
                    st.write(link)

            if github_links:
                st.subheader("🐙 GitHub Links & Members")
                for link in github_links:
                    st.write(link)
                    members = get_github_members(link)
                    if members:
                        st.json(members)
                    else:
                        st.write("No members found or access restricted.")
