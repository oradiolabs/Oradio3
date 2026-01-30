/*!
 * @package:		Oradio3
 * @author url:		https://oradiolabs.nl
 * @author email:	info at oradiolabs dot nl
 * @copyright:		Stichting Oradio, All rights reserved.
 * @license:		GNU General Public License version 3; https://www.gnu.org/licenses/gpl-3.0.html
 */

const allCustomSelects = [];

function initCustomSelect(container) {
    const input = container.querySelector('input');
    const dropdown = container.querySelector('.options');
    let activeIndex = -1;

    const options = () => Array.from(dropdown.querySelectorAll('div'));

    function openDropdown() {
        // Close all other dropdowns
        allCustomSelects.forEach(({ container: c, closeDropdown }) => {
            if (c !== container) closeDropdown();
        });

        if (dropdown.style.display !== 'block') {
            dropdown.style.display = 'block';
            container.classList.add('open');
        }

        const currentValue = input.value;
        const index = options().findIndex(opt => opt.textContent.trim() === currentValue);
        if (index >= 0) setActive(index);
    }

    function closeDropdown() {
        if (dropdown.style.display !== 'none') {
            dropdown.style.display = 'none';
            container.classList.remove('open');
            clearActive();
            activeIndex = -1;
        }
    }

    function clearActive() {
        options().forEach(opt => opt.classList.remove('active'));
    }

    function setActive(index) {
        clearActive();
        const opts = options();
        if (!opts[index]) return;

        opts[index].classList.add('active');
        opts[index].scrollIntoView({ block: 'nearest' });
        activeIndex = index;
    }

    // Close dropdown initially
    closeDropdown();

    // Open dropdown when input is clicked
    input.addEventListener('click', (e) => {
        e.stopPropagation();
        openDropdown();
    });

    // Select option when clicked
    dropdown.addEventListener('click', (e) => {
        e.stopPropagation();
        const option = e.target.closest('div');
        if (!option) return;

        const index = options().indexOf(option); // <- return index instead of value
        input.value = option.textContent.trim();
        input.dispatchEvent(new Event('change', { bubbles: true }));
        closeDropdown();

        if (container._resolveSelection) {
            container._resolveSelection(index);
            container._resolveSelection = null;
        }
    });

    // Promise-based selection
    function getSelectedItem() {
        return new Promise((resolve) => {
            container._resolveSelection = resolve;
        });
    }

    allCustomSelects.push({ container, closeDropdown });

    return getSelectedItem;
}

// Global click listener to close all dropdowns when clicking outside
document.addEventListener('click', () => {
    allCustomSelects.forEach(({ closeDropdown }) => closeDropdown());
});
