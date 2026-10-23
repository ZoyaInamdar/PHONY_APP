# Contributing to Trigger-Aware Player

First off, thank you for your interest in contributing to **Trigger-Aware Player**! 

Trigger-Aware Player is an open-source media player project for Amazon Fire TV running Vega OS. It allows viewers to apply their own preferences to specific moments in a film, such as hiding visual content, muting audio, reducing volume, skipping a scene, or displaying a heads-up warning.

We welcome contributions that improve the player, strengthen detection and evaluation tools, fix bugs, improve documentation, expand test coverage, or make the project easier to use.

Please take a moment to review this guide before getting started.

## 📋 Table of Contents

- [Code of Conduct](#-code-of-conduct)
- [Ways to Contribute](#-ways-to-contribute)
- [Getting Started](#-getting-started)
- [Fork and Clone the Repository](#-fork-and-clone-the-repository)
- [Install Dependencies](#-install-dependencies)
- [Create a Branch](#-create-a-branch)
- [Make Your Changes](#-make-your-changes)
- [Run Tests](#-run-tests)
- [Commit Your Changes](#-commit-your-changes)
- [Submit a Pull Request](#-submit-a-pull-request)
- [Reporting Bugs](#-reporting-bugs)
- [Suggesting Features](#-suggesting-features)
- [Project Guidelines](#-project-guidelines)
- [License](#-license)

## 🤝 Code of Conduct

Be respectful, constructive, and considerate when interacting with other contributors.

- Treat everyone with respect.
- Provide helpful and actionable feedback.
- Welcome different perspectives and experience levels.
- Keep discussions focused on improving the project.
- Do not tolerate harassment, discrimination, or abusive behavior.

## 💡 Ways to Contribute

You do not need to be an expert to contribute. You can help by:

- Fixing bugs or improving existing functionality.
- Improving the React Native player interface.
- Enhancing remote-control and TV navigation support.
- Improving trigger detection and evaluation scripts.
- Adding unit tests and regression tests.
- Improving documentation and setup instructions.
- Reporting compatibility issues with Vega OS.
- Suggesting new viewer-controlled protection options.

If you are new to the project, documentation improvements, reproducible bug reports, and small, well-scoped fixes are great places to start.

## 🚀 Getting Started

Before contributing, make sure you have the tools required for the part of the project you intend to work on.

### Prerequisites

| Tool | Purpose |
|---|---|
| Git | Version control |
| Node.js and npm | JavaScript/TypeScript dependencies and scripts |
| Python 3 | Detection, evaluation, and Python tests |
| Python virtual environment | Isolated Python dependencies |
| Amazon Vega SDK and CLI | Building and running the app for Vega OS |
| A compatible development environment or device | Testing playback and TV interactions |

Use the Node.js version and Vega SDK version recommended by the project or required by its configuration. Some detection and evaluation scripts may require additional Python packages or model files.

## 🍴 Fork and Clone the Repository

### 1. Fork the repository

Open the project repository on GitHub:

**https://github.com/ZOYA-INAMDAR/PHONY_APP**

Replace this URL with the repository's actual canonical GitHub URL if it differs.

Click **Fork** in the upper-right corner of GitHub. This creates a copy of the repository under your GitHub account.

### 2. Clone your fork

Replace `YOUR-USERNAME` with your GitHub username.

```bash
git clone https://github.com/YOUR-USERNAME/PHONY_APP.git
cd PHONY_APP
```

### 3. Add the original repository as `upstream`

Replace `ORIGINAL-OWNER` with the account that owns the original repository.

```bash
git remote add upstream https://github.com/ORIGINAL-OWNER/PHONY_APP.git
git remote -v
```

You should see your fork as `origin` and the original repository as `upstream`.

### 4. Synchronize your fork

Before starting new work, update your local copy from the original repository:

```bash
git fetch upstream
git switch main
git merge upstream/main
git push origin main
```

If your local `main` branch contains uncommitted changes, **save or commit them before synchronizing**. Do not use destructive commands such as `git reset --hard` or `git clean -fd` to resolve conflicts or update your fork unless you fully understand their effects.

If a merge conflict occurs, resolve it carefully before proceeding.

## 📦 Install Dependencies

### 1. Install JavaScript dependencies

From the project root, run:

```bash
npm install
```

If the repository contains a committed `package-lock.json` and you want a reproducible dependency installation, use:

```bash
npm ci
```

Use the command appropriate for the repository's current lockfile and development workflow. Avoid committing unrelated lockfile changes.

### 2. Set up Python dependencies

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Upgrade pip and install the project's Python requirements:

```bash
python -m pip install --upgrade pip
```

If `requirements.txt` is present and contains the dependencies for your task:

```bash
python -m pip install -r requirements.txt
```

Some detection or evaluation tasks may require additional model-specific dependencies. Follow the relevant script documentation and do not assume that every optional model is required for every contribution.

### 3. Set up the Vega development environment

To build and run the application on Vega OS, install and configure the Amazon Vega SDK and CLI according to the official documentation.

Verify that the CLI is available:

```bash
vega --version
```

The exact build and deployment commands may depend on the installed SDK version and target device. Review the repository's existing scripts and package configuration before running deployment commands.

**Note:** You can work on documentation, tests, and many engine-level changes without deploying the application to a Vega device.

## 🌿 Create a Branch

Create a separate branch for your contribution rather than working directly on `main`.

```bash
git switch main
git pull upstream main
git switch -c fix/short-description
```

Choose a descriptive branch name based on your task. Examples:

```text
feature/trigger-action-customization
fix/playback-timing
test/trigger-resolution
docs/contributing-guide
```

If you have existing local changes, make sure they are safe before switching branches.

## 🛠️ Make Your Changes

Before editing code:

1. Read the relevant implementation and existing tests.
2. Identify the expected behavior and affected components.
3. Keep the change focused on one issue or feature.
4. Follow existing naming conventions and code style.
5. Add or update tests when behavior changes.
6. Update documentation when setup, configuration, or behavior changes.

### Project-specific guidelines

**Viewer-controlled actions**

- Respect the viewer's selected action for each supported trigger.
- Preserve the established trigger contracts and default actions.
- Validate action overrides against the permitted actions for each trigger.
- Handle overlapping triggers consistently.
- Ensure temporary audio changes are restored correctly when an interval ends, playback seeks, or another trigger becomes active.
- Do not claim an action is supported unless it is actually implemented and tested.

**Trigger detection and evaluation**

- Keep detection outputs consistent with the expected trigger/index format.
- Document any changes to thresholds, timing, or evaluation methodology.
- Include reproducible tests or evaluation evidence when practical.
- Avoid changing benchmark results merely to make a detector appear more accurate.

**Vega OS and playback**

- Consider remote-control navigation and focus behavior.
- Test playback, seeking, subtitles, and action transitions where applicable.
- Respect platform limitations and document any fallback behavior.
- Avoid unnecessary rewrites of the player or unrelated application components.

**Data and media**

- Do not commit large media files, generated outputs, model weights, credentials, or private datasets unless the project explicitly requires them.
- Follow the repository's existing `.gitignore` and data-management conventions.
- Do not overwrite benchmark inputs or existing evaluation evidence without a clear reason.

## 🧪 Run Tests

Run the checks relevant to your changes before submitting a pull request.

### TypeScript engine checks

If the repository provides the engine TypeScript configuration:

```bash
npx tsc --noEmit --project tsconfig.engine.json
```

### Jest tests

Run the full Jest test suite using the repository's configured test script:

```bash
npm test
```

If that script is not configured for Jest, inspect `package.json` and use the existing test command. You can run focused tests while developing, then run the relevant regression suite before submitting.

### Python tests

If `pytest` is installed:

```bash
python -m pytest
```

For a focused Python test file:

```bash
python -m pytest tests/test_detect_insect.py
```

Replace the example path with the test relevant to your change.

### Vega build

For changes affecting the application or its native integration, use the project's documented Vega build command. For example, if supported by the current configuration:

```bash
npx react-native build-vega --build-type Debug
```

Deploy to a device or virtual device only when needed and available.

### Review your changes

Before committing, inspect the working tree and check for whitespace errors:

```bash
git status --short
git diff --check
git diff
```

Review the output to ensure that you are committing only the intended files. Do not include unrelated changes, generated artifacts, secrets, or local environment files.

If a test or build fails, investigate the failure and document any unresolved issue in your pull request instead of claiming the check passed.

## 📝 Commit Your Changes

Use clear, descriptive commit messages.

Examples:

```bash
git add src/engine/resolveActions.ts test/engine/resolveActions.test.ts
git commit -m "fix: restore audio after trigger interval"
```

```bash
git commit -m "test: cover overlapping trigger actions"
```

```bash
git commit -m "docs: improve contributor setup instructions"
```

These are examples; stage only the files relevant to your contribution.

## 🔁 Submit a Pull Request

After committing your changes:

### 1. Push your branch to your fork

```bash
git push -u origin fix/short-description
```

Replace the branch name with the branch you created.

### 2. Open a pull request

On GitHub, open your fork and select **Compare & pull request**. Set the original repository's `main` branch as the base branch and your feature branch as the compare branch.

### 3. Describe your contribution

A good pull request should include:

- **Summary:** What changed?
- **Motivation:** Why was the change needed?
- **Implementation:** What files or components were affected?
- **Testing:** Which commands were run, and what were the results?
- **Limitations:** What remains untested or unsupported?
- **Screenshots or logs:** Include these when they help demonstrate UI or playback changes.

Keep pull requests focused and small where possible. Be prepared to address review comments and make follow-up changes.

## 🐛 Reporting Bugs

When opening a bug report, include as much of the following information as possible:

- A clear description of the problem.
- Steps to reproduce it.
- Expected behavior versus actual behavior.
- Operating system and relevant Node.js, Python, or Vega SDK versions.
- The affected trigger category or action, if applicable.
- Relevant test output or logs.
- Screenshots or a short recording, when useful.

Please remove API keys, tokens, personal information, and other sensitive data from logs before sharing them.

## ✨ Suggesting Features

Feature suggestions are welcome!

When proposing a feature, explain:

- The problem it solves.
- The intended user experience.
- How it should interact with existing trigger actions and viewer preferences.
- Potential edge cases and platform limitations.
- How the behavior could be tested.

For substantial changes, consider opening an issue or discussing the design before implementation.

## ✅ Project Guidelines

To help keep the project maintainable:

- **Keep changes scoped:** Avoid unrelated refactoring in feature pull requests.
- **Preserve compatibility:** Do not silently change established trigger names, action contracts, or index formats.
- **Test behavior:** Include regression tests for bug fixes and new functionality.
- **Document limitations:** Be transparent about unsupported platform features and unverified behavior.
- **Protect user choice:** Viewer preferences should be respected and predictable.
- **Be reproducible:** Provide commands and test results so others can verify your work.
- **Respect the license:** Ensure that contributed code and assets can legally be distributed under the project's license.
- **Ask when uncertain:** If a change could affect playback, media handling, or existing user settings, discuss the intended behavior before making broad changes.

## 📄 License

By contributing, you agree that your contributions may be distributed under the license used by this repository. Please review the project's `LICENSE` file before submitting code or assets.

Thank you for helping improve Trigger-Aware Player! 