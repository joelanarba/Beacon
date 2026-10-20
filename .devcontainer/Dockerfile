FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

USER root
# Beacon does not use Yarn. Remove its inherited APT source before Features run
# apt-get update; the upstream image's Yarn key can lag repository key rotation.
RUN rm -f /etc/apt/sources.list.d/yarn.list
