# Connect an OpenAI project through Keychain

Use only for an explicitly requested connection in an enrolled Codex checkout.
Confirm that the installed candidate includes this skill and that the intended
macOS session and absolute Python executable passed the disposable Keychain
probe. Loading this guide, inspecting status or rehearsing the local exchange
grants no permission to create a key or start inference.

1. Open the official OpenAI Developers project-key picker. End the turn after
   opening it and wait for the trusted widget result. Preserve its selected
   organization, project, key name and expiry. If the widget or trust provenance
   is unavailable, stop setup.
2. Show the exact selection and the default macOS Keychain destination under
   service `vibe-coding-workflow.openai`. Obtain explicit approval through the
   trusted Codex interaction for creating and storing that key. A direct typed
   approval can confirm this destination when a suitable form is unavailable.
   A decline, missing response, or changed selection stops the attempt. The
   OpenAI Developers env-file form cannot approve Keychain storage.
3. After approval, start
   `/absolute/python -I -B -u scripts/usage_key_setup_host.py --node /absolute/node --cwd /absolute/project/path`
   from the installed package with the qualified Python and Node executables.
   Use a retained input pipe or a dedicated PTY. Wait for `ready:true` before sending
   a single JSON line with `name`, `organization_id`, `project_id`, and the
   selected positive `expires_in_seconds` when present. The bridge suppresses
   echo on its PTY. The caller owns approval and exact selection provenance.
4. Forward the resulting `creation_arguments` once to the official encrypted
   key creation tool. Forward its structured encrypted result to the same
   process as a single line containing `structuredContent` and the tool's
   `isError` flag if present. Keep ciphertext inside the tool-to-process handoff.
   Only public key material goes to the connector. If the result is lost, cancel the
   exchange and reconcile creation before considering another attempt.
5. Use the returned binding from the `mode:off` receipt in any separately
   authorized version 2 configuration. A storage failure after
   creation requires recovery of the existing attempt. Preserve the selected
   metadata for recovery and leave revocation or replacement to a separately
   authorized action.

The bridge accepts no approval field and makes no account calls. A bridge receipt
establishes local storage. Use [explicit activation](procedure.md#set-up-and-select-a-mode)
to select a live mode after setup.
