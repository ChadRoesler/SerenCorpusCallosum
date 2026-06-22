import * as vscode from "vscode";
import { SerenConfig, promptSetToken } from "./config";
import { SerenClient } from "./client";
import { SearchTool } from "./tools";

let statusBar: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const config = new SerenConfig(context.secrets);
  const client = new SerenClient(config);

  // -- status bar -------------------------------------------------------------
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "serenCorpusCallosum.checkHealth";
  statusBar.text = "$(git-merge) CorpusCallosum";
  statusBar.tooltip = "Seren CorpusCallosum - the fan over every hall. Click to check service health.";
  statusBar.show();
  context.subscriptions.push(statusBar);

  // -- commands ---------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand("serenCorpusCallosum.setToken", () =>
      promptSetToken(config)
    ),

    vscode.commands.registerCommand("serenCorpusCallosum.checkHealth", async () => {
      const alive = await client.ping();
      setStatusBar(alive);
      vscode.window.showInformationMessage(
        alive
          ? "Seren CorpusCallosum: service is reachable ✓"
          : "Seren CorpusCallosum: service is not reachable ✗"
      );
    }),

    vscode.commands.registerCommand("serenCorpusCallosum.openViewer", async () => {
      // The Bridge - the federation roster + add/remove stores + live search.
      await vscode.env.openExternal(vscode.Uri.parse(`${config.endpoint}/viewer`));
    }),

    vscode.commands.registerCommand("serenCorpusCallosum.startService", async () => {
      const cmd = config.startCommand;
      const terminal = vscode.window.createTerminal({
        name: "Seren CorpusCallosum",
        hideFromUser: false,
      });
      terminal.show();
      terminal.sendText(cmd);
      context.subscriptions.push(terminal);

      // poll for up to 15s then re-check
      await waitForService(client, 15);
      const alive = await client.ping();
      setStatusBar(alive);
      if (alive) {
        vscode.window.showInformationMessage("Seren CorpusCallosum: service started ✓");
      } else {
        vscode.window.showWarningMessage(
          "Seren CorpusCallosum: service may still be starting - check the terminal."
        );
      }
    })
  );

  // -- register the one LM tool: the federated search -------------------------
  // SCC is the fan, not a store. Its value to Copilot is a single search that
  // reaches every hall at once - so it ships exactly one tool, not a CRUD set.
  context.subscriptions.push(
    vscode.lm.registerTool("seren_corpuscallosum_search", new SearchTool(client))
  );

  // -- startup health check ---------------------------------------------------
  const alive = await client.ping();
  setStatusBar(alive);

  if (!alive && !config.suppressStartPrompt) {
    const choice = await vscode.window.showWarningMessage(
      "Seren CorpusCallosum: service is not reachable. Would you like to start it?",
      "Start Service",
      "Set Endpoint",
      "Don't Ask Again",
      "Dismiss"
    );
    if (choice === "Start Service") {
      vscode.commands.executeCommand("serenCorpusCallosum.startService");
    } else if (choice === "Set Endpoint") {
      vscode.commands.executeCommand(
        "workbench.action.openSettings",
        "serenCorpusCallosum.endpoint"
      );
    } else if (choice === "Don't Ask Again") {
      await config.setSuppressStartPrompt(true);
      vscode.window.showInformationMessage(
        "Seren CorpusCallosum: startup prompt suppressed. " +
        "Toggle 'serenCorpusCallosum.suppressStartPrompt' in settings to re-enable."
      );
    }
  }
}

export function deactivate(): void {
  statusBar?.dispose();
}

// -- helpers ----------------------------------------------------------------

function setStatusBar(alive: boolean): void {
  if (alive) {
    statusBar.text = "$(git-merge) CorpusCallosum ✓";
    statusBar.backgroundColor = undefined;
    statusBar.tooltip = "Seren CorpusCallosum - service reachable";
  } else {
    statusBar.text = "$(git-merge) CorpusCallosum ✗";
    statusBar.backgroundColor = new vscode.ThemeColor(
      "statusBarItem.warningBackground"
    );
    statusBar.tooltip = "Seren CorpusCallosum - service not reachable. Click to check again.";
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForService(client: SerenClient, maxSeconds: number): Promise<void> {
  const deadline = Date.now() + maxSeconds * 1000;
  while (Date.now() < deadline) {
    await sleep(1000);
    if (await client.ping()) {
      return;
    }
  }
}
