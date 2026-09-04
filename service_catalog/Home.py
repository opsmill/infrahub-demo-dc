"""Infrahub Service Catalog - Main Application Entry Point.

This is the main entry point for the Infrahub Service Catalog application.
It uses st.navigation to create a hierarchical menu structure.
"""

import streamlit as st  # type: ignore[import-untyped]
from utils import display_logo

# Configure page layout - must be first Streamlit command
st.set_page_config(
    page_title="Infrahub Service Catalog",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)

# Display logo in sidebar
display_logo()

# Define pages with hierarchical structure
home_page = st.Page("pages/0_Dashboard.py", title="Home", icon="🏠", default=True, url_path="dashboard")

service_catalog_pages = [
    st.Page("pages/1_Create_DC.py", title="Create DC", icon="🏗️"),
    st.Page("pages/2_Create_VPN.py", title="Create VPN", icon="🔗"),
    st.Page("pages/4_Create_VM.py", title="Create VM", icon="🖥️"),
]

visualization_pages = [
    st.Page("pages/3_Rack_Visualization.py", title="Rack Visualization", icon="🗄️"),
]

# Create navigation with sections
pg = st.navigation(
    {
        "": [home_page],
        "Service Catalog": service_catalog_pages,
        "Visualizations": visualization_pages,
    }
)

# Run the selected page
pg.run()
