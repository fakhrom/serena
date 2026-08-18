# The Dashboard and GUI Tool

Serena comes with built-in tools for monitoring and managing the current session:

* the **web-based dashboard** (enabled by default)
  
  The dashboard provides detailed information on your Serena session, the current configuration and provides access to logs.
  Some settings (e.g. the current set of active programming languages) can also be directly modified through the dashboard.

  The dashboard is supported on all platforms.
  
  By default, it will be accessible at `http://localhost:24282/dashboard/index.html`,
  but a higher port may be used if the default port is unavailable/multiple instances are running.
  See [Running Several Servers at Once](several-servers) if you do run several and want each
  dashboard to keep a stable port.

  **We recommend always enabling the dashboard**. If you don't want the browser to open automatically,
  you can disable it while still keeping the dashboard running in the background (see below).

* the **GUI tool** (disabled by default)
  
  The GUI tool is a native application window which displays logs.
  It furthermore allows you to shut down the agent and to access the dashboard's URL (if it is running). 

  This is mainly supported on Windows, but it may also work on Linux; macOS is unsupported.

Both can be configured in Serena's [configuration](050_configuration) file (`serena_config.yml`).
If enabled, they will automatically be opened as soon as the Serena agent/MCP server is started.
For the dashboard, this can be disabled if desired (see below).

(several-servers)=
## Running Several Servers at Once

Serena servers started in stdio mode are independent processes: several can run against
the same project at the same time, for example when two agents work on one repository,
or when one agent runs two sessions on different models. Each starts its own dashboard,
and each takes the first free port from 24282 upwards, so they never collide.

What they do *not* get automatically is a STABLE assignment. The port depends on which
server started first, so restarting the pair in the other order swaps the two
dashboards, and a bookmarked URL then shows the other session. Two ways to fix that:

* **Name the client.** Set the `SERENA_CLIENT_LABEL` environment variable on each server,
  e.g. `SERENA_CLIENT_LABEL=opencode:deepseek`. The label is shown as *Current Client* in
  the dashboard's configuration panel, and the dashboard port is derived from it, so the
  same label always lands on the same port regardless of start order. This is the option
  to prefer when the servers are launched by a client whose own identity you control,
  since it makes the dashboard self-identifying as well as stable.

* **Let Serena ask who its client is.** Set `client_label_command` in `serena_config.yml`
  to a command that prints the label. Serena runs it once at startup and uses the last
  non-empty line. This suits clients that spawn Serena themselves: the command can look at
  the process that spawned it and work out both which client and which model, so one
  setting serves every client without naming any of them. It is off unless set, and
  `SERENA_CLIENT_LABEL` takes precedence, so an explicitly labelled server never runs it.

* **Fix the port explicitly.** Set `web_dashboard_port` in `serena_config.yml`, pass
  `--web-dashboard-port` to `start-mcp-server`, or set the `SERENA_DASHBOARD_PORT`
  environment variable. An explicit port takes precedence over a derived one.

In every case, an occupied port still falls through to the next free one, so neither
setting can prevent a server from starting.

:::{tip}
If you want the servers to keep entirely separate configuration, logs and language-server
data as well, give each its own data directory via the `SERENA_HOME` environment variable
(see [Serena Data Directory](050_configuration.md#serena-data-directory)). This is not
required for concurrent operation -- it is only needed if you want the settings themselves
to differ.
:::

## Disabling Automatic Browser Opening

If you prefer not to have the dashboard open automatically (e.g., to avoid focus stealing), you can disable it
by setting `web_dashboard_open_on_launch: False` in your `serena_config.yml` or by passing `--open-web-dashboard False`
to `start-mcp-server` CLI command.

When automatic opening is disabled, you can still access the dashboard by:
* asking the LLM to "open the Serena dashboard", which will open the dashboard in your default browser
  (the tool `open_dashboard` is enabled for this purpose, provided that the dashboard is active, 
  not opened by default and the GUI tool, which can provide the URL, is not enabled)
* navigating directly to the URL (see above)
