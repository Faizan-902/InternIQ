"""
InternIQ Streamlit User Interface
Owner: Samriddhi Raj (Team HumanBot)
Status: Interface Stub
"""

import sys
from pathlib import Path

import streamlit as st

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main():
    st.set_page_config(page_title="InternIQ - Internship Assistant", layout="wide")
    st.title("InternIQ: Agentic Internship Assistant")
    st.markdown(
        "Welcome to InternIQ. This interface connects the LangGraph agent core, "
        "vetting, ranking, document generation, and application tracker."
    )
    st.info("System is initializing. Core verification modules are fully active.")


if __name__ == "__main__":
    main()
