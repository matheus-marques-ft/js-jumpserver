// content_script.js

const debug = console.log

// Create a Mutation Observer instance
const observer = new MutationObserver(function (mutationsList) {
    // Iterate over each mutation that occurred
    for (let mutation of mutationsList) {
        // Check whether any nodes were added
        if (mutation.type === 'childList') {
            // Get all <a> tag elements
            const links = document.getElementsByTagName('a');

            // Iterate over the <a> tag elements and modify their link attributes
            debug("Start replacing tags")
            for (let i = 0; i < links.length; i++) {
                links[i].target = '_self'; // Set the target attribute to _self, opening in the current window
            }

            // Stop observing, the replacement operation is complete
            observer.disconnect();

            // Exit the loop, no longer processing subsequent mutations
            break;
        }
    }
});

// Start observing changes to document.body's child nodes
observer.observe(document.body, {childList: true, subtree: true});

document.addEventListener("contextmenu", function (event) {
    debug('On context')
    event.preventDefault();
});

const AllowedKeys = ['P', 'F', 'C', 'V']
window.addEventListener("keydown", function (e) {
    if (e.key === "F12" || e.key === "F1" || (e.ctrlKey && !AllowedKeys.includes(e.key.toUpperCase()))) {
        e.preventDefault();
        e.stopPropagation();
        debug('Press key: ', e.ctrlKey ? 'Ctrl' : '', e.shiftKey ? ' Shift' : '', e.key)
    }
}, true);

// Override the window.open function
window.open = function (url, target, features) {
    // Force target to "_self" so the new page opens in the current tab
    target = "_self";
    debug('Open url: ', url, target, features)
    // Call the original window.open function
    window.href = url
    // return originalOpen.call(this, url, target, features);
};
