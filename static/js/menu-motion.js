/* Optional enhancement: content is visible and usable before GSAP loads. */
(() => {
  if (!window.gsap || !window.ScrollTrigger) return;
  gsap.registerPlugin(ScrollTrigger);
  const media = gsap.matchMedia();
  media.add({ desktop: '(min-width: 1100px)', motion: '(prefers-reduced-motion: no-preference)' }, context => {
    if (!context.conditions.motion) return;
    const photos = document.querySelectorAll('.featured .product-thumb');
    photos.forEach(photo => {
      gsap.fromTo(photo, { scale: 0.96 }, {
        scale: 1, ease: 'none',
        scrollTrigger: { trigger: photo, start: 'top bottom', end: 'top 55%', scrub: 0.5, invalidateOnRefresh: true },
      });
    });
    const identity = document.querySelector('.info-identity');
    const content = document.querySelector('.info-content');
    if (context.conditions.desktop && identity && content) {
      ScrollTrigger.create({
        trigger: identity, pin: identity, pinSpacing: false,
        start: 'top 104px', endTrigger: content, end: 'bottom bottom',
        invalidateOnRefresh: true,
      });
    }
    const refresh = () => ScrollTrigger.refresh();
    document.querySelectorAll('.menu-section').forEach(section => section.addEventListener('toggle', refresh));
    window.addEventListener('load', refresh, { once: true });
    document.fonts?.ready.then(refresh);
    return () => {
      document.querySelectorAll('.menu-section').forEach(section => section.removeEventListener('toggle', refresh));
      window.removeEventListener('load', refresh);
    };
  });
})();
