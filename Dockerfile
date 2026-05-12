# Build normally on x86_64 (Glama CI, Linux servers).
# On Apple Silicon: docker build --platform linux/amd64 -t freecad-mcp .
FROM mambaorg/micromamba:latest

ARG FREECAD_VERSION=1.0.0

USER root

# Install FreeCAD via conda-forge (this is what the AppImages are built from)
RUN micromamba create -p /opt/freecad -c conda-forge \
    freecad=${FREECAD_VERSION} \
    --yes \
    && micromamba clean -a --yes

# Install MCP bridge Python dependencies into the same env
RUN micromamba run -p /opt/freecad pip install --no-cache-dir "mcp>=1.27.1"

# Copy MCP server
COPY freecad_mcp_server.py mcp_bridge_framing.py /opt/freecad-mcp/
COPY AICopilot/ /opt/freecad-mcp/AICopilot/

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV FREECAD_MCP_FREECAD_BIN=/opt/freecad/bin/freecadcmd
ENV QT_QPA_PLATFORM=offscreen
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/freecad/bin:${PATH}"

LABEL org.opencontainers.image.source="https://github.com/blwfish/freecad-mcp"
LABEL org.opencontainers.image.description="MCP server to control FreeCAD from Claude"
LABEL org.opencontainers.image.licenses="LGPL-2.1-or-later"
LABEL io.modelcontextprotocol.server.name="io.github.blwfish/freecad-mcp"

ENTRYPOINT ["docker-entrypoint.sh"]
