# **Veil** — Automate the Web, Invisibly.

[![Veil Demo](https://img.shields.io/badge/Demo-Live-brightgreen)](https://veil.sh/demo)
[![GitHub Stars](https://img.shields.io/github/stars/veil/veil?style=social)](https://github.com/veil/veil)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Veil is the next evolution of browser automation.** A powerful, upgraded fork of [browser-use](https://github.com/browser-use/browser-use) (⭐ 81k), built for developers who need reliable, scalable, and **invisible** web automation. Stop fighting anti-bot systems. Start automating with confidence.

## The Veil Upgrade: Why Switch?

| Feature | browser-use | **Veil** |
| :--- | :---: | :---: |
| **Stealth & Anti-Detection** | Basic | **Advanced**<br>Fingerprint randomization, human-like interaction delays, anti-bot bypass |
| **Visual Debugging** | Console logs only | **Agent Debugger**<br>Record sessions, step-through logic, inspect DOM interactions visually |
| **Extensibility** | Manual scripts | **Plugin Marketplace**<br>Pre-built skills for login, data extraction, form filling, and more |
| **Session Management** | Limited | **Robust**<br>Persistent contexts, proxy rotation, cookie management |
| **Community & Ecosystem** | Large | **Growing & Specialized**<br>Focused on undetectable automation and developer experience |

## Quickstart: See the Magic in 30 Seconds

```python
from veil import Agent, StealthMode, Debugger

# 1. Create an agent with Stealth Mode enabled
agent = Agent(
    task="Extract the top 5 trending GitHub repositories",
    stealth_mode=StealthMode.HUMAN_LIKE,  # Makes actions undetectable
    debugger=Debugger.VISUAL              # Launches the visual debugger
)

# 2. Run the agent - it handles everything invisibly
results = agent.run()

# 3. View results and debug session in the visual debugger
print(results)
# The debugger automatically opens at http://localhost:9222
```

**That's it.** No more complex setups for anti-detection. No more guessing why your script failed. Just clean, debuggable, invisible automation.

## Architecture Overview

Veil is built on a modular, plugin-first architecture designed for scale and stealth.

```
┌─────────────────────────────────────────────────────────┐
│                     Your Code                           │
│                  (Simple, Clean API)                     │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   Veil Core Engine                       │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│  │  Stealth   │  │  Agent     │  │  Plugin    │        │
│  │  Manager   │  │  Debugger  │  │  Loader    │        │
│  └────────────┘  └────────────┘  └────────────┘        │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Browser Automation Layer                    │
│  (Enhanced Playwright/Puppeteer with Stealth Patches)   │
└─────────────────────────────────────────────────────────┘
```

**Key Components:**
- **Stealth Manager:** Dynamically rotates fingerprints, mimics human mouse movements, and manages interaction timing.
- **Agent Debugger:** A Chrome DevTools-like interface for recording, replaying, and debugging automation sessions.
- **Plugin System:** Extend Veil's capabilities with community-built skills. Install with `veil install <plugin-name>`.

## Installation

Get started in under a minute.

### Prerequisites
- Python 3.9+
- Node.js 18+ (for the visual debugger)

### Install via pip
```bash
# Install the core package
pip install veil-automation

# Install the visual debugger (optional but recommended)
npm install -g @veil/debugger
```

### Or, build from source
```bash
git clone https://github.com/veil/veil.git
cd veil
pip install -e .
```

### Verify Installation
```bash
veil --version
# Should output: veil version 1.0.0
```

## The Plugin Marketplace

Extend Veil instantly with pre-built, community-vetted skills.

```bash
# Browse available plugins
veil search plugins

# Install a popular plugin
veil install login-with-google
veil install extract-product-data
veil install fill-checkout-form
```

**Popular Plugins:**
- `stealth-proxies` — Automatically rotate premium residential proxies.
- `captcha-solver` — Integrate with solving services seamlessly.
- `data-pipeline` — Export scraped data directly to CSV, JSON, or databases.

## Why Developers Are Switching

> *"We reduced our bot detection rate from 40% to under 2%. The visual debugger alone saved us 20 hours a week in debugging."*
> — **Senior Automation Engineer, Fortune 500 E-commerce**

> *"The plugin marketplace turned a week-long project into an afternoon. We just plugged in the 'login-with-google' skill and it worked."*
> — **Lead Developer, Data Analytics Startup**

## Contributing

We welcome contributions! Veil is built by the community, for the community.

1. Check out our [Contributing Guide](CONTRIBUTING.md).
2. Look for issues labeled [`good first issue`](https://github.com/veil/veil/labels/good%20first%20issue).
3. Join our [Discord community](https://discord.gg/veil) for discussion.

## License

Veil is [MIT Licensed](LICENSE).

---

**Ready to automate without a trace?**  
[Get Started](https://veil.sh/docs) | [View Demo](https://veil.sh/demo) | [Join Discord](https://discord.gg/veil)