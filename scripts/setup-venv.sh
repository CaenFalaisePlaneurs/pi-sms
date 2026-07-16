#!/bin/bash
# Setup script for creating a Python virtual environment for local development

set -e

PYTHON_VERSION="${1:-3.13.5}"

echo "Setting up Python virtual environment for pi-sms..."
echo "Target Python version: ${PYTHON_VERSION}"

if command -v "python${PYTHON_VERSION}" &> /dev/null; then
    PYTHON_CMD="python${PYTHON_VERSION}"
else
    echo "Warning: Python ${PYTHON_VERSION} not found. Using system python3."
    PYTHON_CMD="python3"
fi

echo "Using: $(${PYTHON_CMD} --version)"

echo "Creating virtual environment..."
${PYTHON_CMD} -m venv venv

echo "Activating virtual environment and upgrading pip..."
source venv/bin/activate
pip install --upgrade pip setuptools wheel

echo "Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "Virtual environment created successfully!"
echo ""
echo "To activate the virtual environment, run:"
echo "  source venv/bin/activate"
echo ""
echo "To deactivate, run:"
echo "  deactivate"
echo ""
echo "To install dev dependencies, run:"
echo "  pip install -r requirements-dev.txt"
