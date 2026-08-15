class HeroCarousel {
    constructor(container, intervalMs = 5000) {
        this.images = Array.from(container.querySelectorAll('.hero-carousel-image'));
        this.index = this.images.findIndex((image) => image.classList.contains('active'));
        if (this.index < 0) this.index = 0;
        this.intervalMs = intervalMs;
        this.timer = null;

        const prevButton = container.querySelector('.hero-carousel-control.prev');
        const nextButton = container.querySelector('.hero-carousel-control.next');
        if (prevButton) prevButton.addEventListener('click', () => this.manualGoTo(this.index - 1));
        if (nextButton) nextButton.addEventListener('click', () => this.manualGoTo(this.index + 1));

        if (this.images.length > 1) this.startAutoplay();
    }

    startAutoplay() {
        this.timer = setInterval(() => this.goTo(this.index + 1), this.intervalMs);
    }

    manualGoTo(newIndex) {
        this.goTo(newIndex);
        if (this.timer) {
            clearInterval(this.timer);
            this.startAutoplay();
        }
    }

    goTo(newIndex) {
        this.images[this.index].classList.remove('active');
        this.index = (newIndex + this.images.length) % this.images.length;
        this.images[this.index].classList.add('active');
    }
}

document.addEventListener('DOMContentLoaded', function () {
    const hero = document.querySelector('.container.top.hero');
    if (hero) new HeroCarousel(hero);
});
