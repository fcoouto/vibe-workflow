# Codex startup implementation

Read the [shared startup contract](startup.md) when implementing, diagnosing or preparing enrollment. It defines the record format, runtime limits and package/fixture checks.

Codex 0.163.0 and desktop-bundled 0.164.0-alpha.11.1 discover these hooks only with the compatibility manifest. A root `plugin.json` suppresses discovery in both tested builds, including with a compatibility overlay. Requalify before changing that packaging.

Package validation and fixture tests establish package behavior only. They do not establish native execution or qualification.

For client qualification, install the reviewed package into a disposable Codex environment, inspect its exact definition with `/hooks` or `hooks/list`, trust that definition, and observe startup in an enrolled fixture and an unrelated repository. Verify changed/untrusted definitions remain skipped. Record the executable version, surface, package digest and observed output. CLI or app-server evidence does not establish desktop UI behavior or an agent's policy compliance.

The [official hook contract](https://learn.chatgpt.com/docs/hooks) defines payloads and trust; [plugin packaging](https://developers.openai.com/plugins/build/plugins#hooks) defines registration and `PLUGIN_ROOT`. Installing the plugin does not grant hook trust.
