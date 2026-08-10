import { existsSync, mkdirSync, writeFileSync, unlinkSync, readFileSync } from "fs"
import { join, dirname } from "path"
import { fileURLToPath } from "url"
import { homedir } from "os"
import { execFileSync } from "child_process"

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = join(PLUGIN_DIR, "..", "..")
const STATE_DIR = join(homedir(), ".opencode", "state", "worktrail-suggest-next")

/**
 * Strip a skill file's YAML frontmatter and return its instruction body, so a
 * command template can embed the real procedure instead of pointing at a path
 * the model would otherwise have to glob-search for and read itself.
 */
function skillBody(skillName) {
  const text = readFileSync(join(REPO_ROOT, "skills", skillName, "SKILL.md"), "utf-8")
  const parts = text.split("\n---\n")
  return (parts.length > 1 ? parts.slice(1).join("\n---\n") : text).trim()
}

const OPENSPEC_PROPOSE_INSTRUCTIONS = skillBody("openspec-propose")

const SESSION_END_INSTRUCTION = `SESSION WRAP-UP — proactive next-step suggestion (auto-triggered by the Worktrail session.idle hook).

This session's workspace shows file changes. Before you finish, offer YOUR creative "here's what I'd do next": give 1-3 forward-looking, ranked ideas tied to what actually changed this session, focused on valuable step-change improvements.

Decide whether the single strongest idea clears an EXCEPTIONAL-VALUE gate. Creating a handoff is optional, not the default. Capture only a genuine step-change, recurring high-cost bottleneck, material user-outcome improvement, or verified major reliability, security, or operational risk.

Do NOT capture routine polish, nearby cleanup/refactors, extra tests or docs, speculative flexibility, minor optimizations, or the next obvious task. If no idea clears the gate, say 'No handoff captured; no exceptional next step identified.' and finish.

Only if one idea clearly passes the gate, run \`worktrail-handoff --focus "<focus>" --json\` and report its filename. Keep the response tight.`

function hasSubstantiveWork(directory) {
  try {
    return execFileSync("git", ["status", "--porcelain"], {
      cwd: directory,
      encoding: "utf-8",
      timeout: 5000,
    }).trim().length > 0
  } catch {
    return false
  }
}

function sentinelPath(sessionId) {
  return join(STATE_DIR, `${sessionId}.done`)
}

/**
 * Native OpenCode command surface for Worktrail.
 *
 * The package owns the orchestration implementation; these commands are thin
 * prompt adapters that tell OpenCode to use the installed Worktrail console
 * scripts. No external checkout or plugin path is required.
 */
export const Worktrail = async ({ directory } = {}) => ({
  "session.idle": async (input, output) => {
    const sessionId = input?.session?.id || "unknown"
    const sentinel = sentinelPath(sessionId)
    if (existsSync(sentinel) || !hasSubstantiveWork(directory || process.cwd())) return
    mkdirSync(STATE_DIR, { recursive: true })
    writeFileSync(sentinel, "1", "utf-8")
    output.context = output.context || []
    output.context.push(SESSION_END_INSTRUCTION)
  },

  "session.compacted": async (input) => {
    const sessionId = input?.session?.id
    if (sessionId) {
      const sentinel = sentinelPath(sessionId)
      if (existsSync(sentinel)) unlinkSync(sentinel)
    }
  },

  config: async (input) => {
    input.command = input.command || {}
    input.command["worktrail.go"] = {
      description: "Route engineering work through Worktrail",
      template: [
        "Use Worktrail for this request.",
        "Run `worktrail-dashboard` when orientation is needed, then follow the installed",
        "Worktrail workflow. Use `drain` for unattended queue processing and preserve the exact request below.",
        "\nRequest: $ARGUMENTS",
      ].join("\n"),
    }
    input.command["worktrail.handoff"] = {
      description: "Create a Worktrail handoff brief",
      template: [
        "Create a handoff with the Worktrail CLI, not by writing Markdown directly.",
        "Run `worktrail-handoff --focus \"$ARGUMENTS\" --json`, then report the created path.",
      ].join("\n"),
    }
    input.command["worktrail.spec-create"] = {
      description: "Create a DevKit or OpenSpec scaffold through Worktrail",
      template: [
        "Use `worktrail-spec-create` to create the requested spec scaffold.",
        "Choose OpenSpec by default unless the user explicitly requests DevKit format.",
        "\nRequest: $ARGUMENTS",
      ].join("\n"),
    }
    input.command["openspec-propose"] = {
      description: "Propose a new OpenSpec change with all planning artifacts",
      template: [OPENSPEC_PROPOSE_INSTRUCTIONS, "\nRequest: $ARGUMENTS"].join("\n"),
    }
  },
})
