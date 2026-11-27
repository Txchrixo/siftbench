const sectionLinks = document.querySelectorAll('.site-header nav a[href^="#"]');

function showSection() {
  const current = location.hash || '#scores';
  for (const link of sectionLinks) {
    if (link.hash === current) {
      link.setAttribute('aria-current', 'location');
    } else {
      link.removeAttribute('aria-current');
    }
  }

  const disclosure = document.getElementById(current.slice(1));
  if (disclosure instanceof HTMLDetailsElement) {
    disclosure.open = true;
  }
}

showSection();
window.addEventListener('hashchange', showSection);
