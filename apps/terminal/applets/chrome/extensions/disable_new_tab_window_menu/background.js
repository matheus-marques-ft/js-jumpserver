// background.js

const tabs = []
const debug = console.log

// Listen for tab creation events
chrome.tabs.onCreated.addListener(function (tab) {
    // Get all tabs in the current window
    debug('New tab add, tabs : ', tabs.map(t => t.id))
    tabs.push(tab)
});

chrome.tabs.onUpdated.addListener(function (tabId, changeInfo, tab) {
    debug('Tab status changed: ', tabId, ' => ', changeInfo.status)
    if (changeInfo.status !== 'loading') {
        return
    }
    const tabFind = tabs.findIndex(t => t.id === tabId)
    if (tabFind === -1) {
        tabs.push(tab)
    } else {
        Object.assign(tabs[tabFind], tab)
    }

    const blockUrls = ['chrome://newtab/']
    if (!tab.url || blockUrls.includes(tab.url) || tab.url.startsWith('chrome://')) {
        alert('Safe mode: opening new tabs is not allowed')
        debug('Blocked url, destroy: ', tab.url)
        chrome.tabs.remove(tabId);
        return
    }

    // No restriction on the first tab
    // Update the initial tab's state, since the first tab has no address bar and can navigate freely
    if (tabs.length === 1) {
        debug('First tab, pass')
        return
    }

    const firstUrl = tabs[0].url
    const curUrl = tab.url
    if (!firstUrl.startsWith('http') || !curUrl.startsWith('http')) {
        debug('First tab url empty, or current empty, pass ', firstUrl, curUrl)
        return
    }

    const firstTabHost = new URL(firstUrl).host
    const curHost = new URL(curUrl).host
    const firstDomain = firstTabHost.split('.').slice(-2).join('.')
    const curDomain = curHost.split('.').slice(-2).join('.')
    if (firstDomain !== curDomain) {
        debug('Not same domain, destroy: ', firstTabHost, ' current: ', curHost)
        chrome.tabs.remove(tabId);
    }
})

chrome.tabs.onRemoved.addListener(function (tabId, removeInfo) {
    debug('Tab removed: ', tabId)
    const tabFind = tabs.findIndex(t => t.id === tabId)
    if (tabFind !== -1) {
        tabs.splice(tabFind, 1)
    }
})
