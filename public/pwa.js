'use strict';
(() => {
  const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  if (standalone()) return;
  let installPrompt;
  const button = document.createElement('button');
  button.className = 'runback-install';
  button.textContent = 'Install RUNBACK';
  const dialog = document.createElement('dialog');
  dialog.className = 'runback-install-dialog';
  dialog.innerHTML = '<h2>RUNBACK on your home screen</h2><p>iPhone or iPad: open this site in Safari, tap Share, then Add to Home Screen. Enable Open as Web App if offered.</p><p>Android: open this site in Chrome, tap the three-dot menu, then Install app or Add to Home screen.</p><p>Computer: look for the install icon in your browser’s address bar or menu.</p><p class="muted">An internet connection is needed for profiles, messages and sessions.</p><form method="dialog"><button class="primary">Got it</button></form>';
  document.body.append(button, dialog);
  button.addEventListener('click', async () => {
    if (installPrompt) {
      const prompt = installPrompt;
      installPrompt = null;
      await prompt.prompt();
      const result = await prompt.userChoice;
      if (result.outcome === 'accepted') button.remove();
    } else dialog.showModal();
  });
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installPrompt = event;
  });
  window.addEventListener('appinstalled', () => { button.remove(); dialog.close(); });
})();
