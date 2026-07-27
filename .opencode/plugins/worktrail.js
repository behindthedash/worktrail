/**
 * Native OpenCode command surface for Worktrail.
 *
 * The package owns the orchestration implementation; these commands are thin
 * prompt adapters that tell OpenCode to use the installed Worktrail console
 * scripts. No external checkout or plugin path is required.
 */
export const Worktrail = async () => ({
  config: async (input) => {
    input.command = input.command || {}
    input.command["worktrail.go"] = {
      description: "Route engineering work through Worktrail",
      template: [
        "Use Worktrail for this request.",
        "Run `worktrail-dashboard` when orientation is needed, then follow the installed",
        "Worktrail workflow and preserve the exact request below.",
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
    input.command["worktrail.drain"] = {
      description: "Drain queued Worktrail handoffs",
      template: [
        "Use the installed Worktrail drain command for this request.",
        "Run `worktrail-drain` with the user's requested limits and report its stop reason.",
        "\nRequest: $ARGUMENTS",
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
  },
})
