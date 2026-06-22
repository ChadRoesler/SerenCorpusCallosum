import * as vscode from "vscode";
import { SerenConfig, promptSetToken } from "./config";
import { SerenClient } from "./client";
import {
  SetFactTool,
  GetFactTool,
  SearchTool,
  ForgetFactTool,
  HistoryTool,
  ListFactsTool,
} from "./tools";

let statusBar: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const config = new SerenConfig(context.secrets);
  const client = new SerenClient(config);

  // -- status bar -------------------------------------------------------------
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "serenCorpusCallosum.checkHealth";
  statusBar.text = "$(database) CorpusCallosum";
  statusBar.tooltip = "Seren CorpusCallosum - click to check service health";
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

  // -- register LM tools (the CorpusCallosum 6) -----------------------------------------
  context.subscriptions.push(
    vscode.lm.registerTool("seren_corpuscallosum_set_fact", new SetFactTool(client)),
    vscode.lm.registerTool("seren_corpuscallosum_get_fact", new GetFactTool(client)),
    vscode.lm.registerTool("seren_corpuscallosum_search", new SearchTool(client)),
    vscode.lm.registerTool("seren_corpuscallosum_forget_fact", new ForgetFactTool(client)),
    vscode.lm.registerTool("seren_corpuscallosum_history", new HistoryTool(client)),
    vscode.lm.registerTool("seren_corpuscallosum_list_facts", new ListFactsTool(client))
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
    statusBar.text = "$(database) CorpusCallosum ✓";
    statusBar.backgroundColor = undefined;
    statusBar.tooltip = "Seren CorpusCallosum - service reachable";
  } else {
    statusBar.text = "$(database) CorpusCallosum ✗";
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
