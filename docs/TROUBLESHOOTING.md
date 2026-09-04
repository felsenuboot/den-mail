# Troubleshooting

- **The light theme looks half dark.** Wallpaper theming tools (Matugen, pywal,
  ML4W) write `~/.config/gtk-4.0/colors.css`, which repaints every GTK app. The
  app re-asserts libadwaita's light palette, so Light means light.
- **Label colours differ from the web app.** Fastmail's public JMAP API does not
  expose them, so the app assigns its own. Right-click a label → Colour.
- **Clicking a link does not bring the browser forward.** That is the
  compositor's call (xdg-activation). On Hyprland enable
  `misc.focus_on_activate`, or turn on "Open links in a new browser window" in
  Preferences → Reading.
- **HTML mail shows up blank.** WebKit's DMA-BUF renderer is already disabled,
  which fixed this on an NVIDIA/Wayland setup. If it still happens, open an
  issue with your GPU and driver.
- **Aliases cannot be created in the app.** Fastmail's public API does not
  allow it (only Masked Email), so the app links to the web settings.
