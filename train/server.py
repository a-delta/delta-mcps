import logging
import os
import random
import sys
import requests
from mcp.server.fastmcp import FastMCP

# Set up logging
name = "delta-mcp-server"
logging.basicConfig(
    level=logging.INFO,
    format='%(name)s -%(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(name)

# St up MCP Server
port = int(os.environ.get('PORT', 8080))
mcp = FastMCP(name, logger=logger, port=port)

@mcp.tool()
def add(a: int, b: int) -> int:
    """add two numbers"""
    logger.info(f"Tool called: add({a}, {b})")
    return a + b
@mcp.tool()
def get_secret_word() -> str:
    """Get a random secret word"""
    logger.info("Tool called: get_secret_word()")
    return random.choice(["apple", "banana", "cherry"])

@mcp.tool()
def get_current_weather(city: str) -> str:
    """Get current weather for a city"""
    logger.info(f"Tool Called: get_current_weather({city})")

    try:
        endpoint = "https://wttr.in"
        response = requests.get(f"{endpoint}/{city}", timeout=10)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error fetching weather data: {str(e)}")
        return f"Error fetching weather data: {str(e)}"
    
if __name__ == "__main__":
    logger.info(f"Starting MCP Server on port {port}...")
    try:
        mcp.run(transport="sse")
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
        sys.exit(1)
    finally:
        logger.info("Server terminated")