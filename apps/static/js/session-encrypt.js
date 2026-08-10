function fillKey(key) {
    const keyLength = 16
    if (key.length > keyLength) {
        key = key.slice(0, keyLength)
    }
    const filledKey = Buffer.alloc(keyLength)
    const keys = Buffer.from(key)
    for (let i = 0; i < keys.length; i++) {
        filledKey[i] = keys[i]
    }
    return filledKey
}

function aesEncrypt(text, originKey) {
    const key = CryptoJS.enc.Utf8.parse(fillKey(originKey));
    return CryptoJS.AES.encrypt(text, key, {
        mode: CryptoJS.mode.ECB,
        padding: CryptoJS.pad.ZeroPadding
    }).toString();
}

function rsaEncrypt(text, pubKey) {
    if (!text) {
        return text
    }
    const jsEncrypt = new JSEncrypt();
    jsEncrypt.setPublicKey(pubKey);
    return jsEncrypt.encrypt(text);
}

function rsaDecrypt(cipher, pkey) {
    const jsEncrypt = new JSEncrypt();
    jsEncrypt.setPrivateKey(pkey);
    return jsEncrypt.decrypt(cipher)
}


window.rsaEncrypt = rsaEncrypt
window.rsaDecrypt = rsaDecrypt

function hexToBytes(hex) {
    if (!hex) return new Uint8Array([])
    hex = hex.toString().trim().toLowerCase()
    if (hex.startsWith('0x')) {
        hex = hex.slice(2)
    }
    // Ensure the length is even
    const len = Math.floor(hex.length / 2)
    const bytes = new Uint8Array(len)
    for (let i = 0; i < len; i++) {
        bytes[i] = parseInt(hex.substr(i * 2, 2), 16)
    }
    return bytes
}

function bytesToBase64(bytes) {
    // Uint8Array -> base64 (standard base64)
    let binary = ''
    for (let i = 0; i < bytes.length; i++) {
        binary += String.fromCharCode(bytes[i])
    }
    return btoa(binary)
}

function rsaEncryptPassword(password,  rsaPublicKey) {
    const aesKey = (Math.random() + 1).toString(36).substring(2)
    // The public key is stored as base64
    const keyCipher = rsaEncrypt(aesKey, rsaPublicKey)
    const passwordCipher = aesEncrypt(password, aesKey)
    return `${keyCipher}:${passwordCipher}`
}

function ensureSm2PublicKey(sm2PublicKey) {
    // sm2.min.js's doEncrypt needs a public key that decodePointHex can parse:
    // usually an uncompressed point hex, in the format `04||x||y` (total length 130).
    // But the public key generated/issued by the backend is sometimes `x||y` (length 128); this normalizes it by padding with a `04` prefix.
    if (typeof sm2PublicKey === 'string') {
        sm2PublicKey = sm2PublicKey.replaceAll('"', '').trim()
        if (sm2PublicKey.startsWith('0x')) {
            sm2PublicKey = sm2PublicKey.slice(2)
        }
        // The SM2 public key issued by the backend is commonly x||y (128 hex); sm-crypto needs 04||x||y (130 hex)
        if (sm2PublicKey.length === 128 && !sm2PublicKey.startsWith('04')) {
            sm2PublicKey = '04' + sm2PublicKey
        }
    }
    return sm2PublicKey
}


function gmEncryptPassword(password,  sm2PublicKey) {
    sm2PublicKey = ensureSm2PublicKey(sm2PublicKey)
    // Only adapting the frontend, not changing the backend:
    // generate the 16-character key directly (the backend's padding_key stays as-is, no further padding)
    const sm4KeyRaw = randomString(16)
    const sm4KeyHex = Buffer.from(sm4KeyRaw).toString('hex')

    let keyCipher = ''
    try {
        // Aligned with the backend gmssl.sm2.CryptSM2 default decrypt mode:
        // gmssl parses the format C1C2C3 (mode=0), so the frontend output here also uses mode=0.
        keyCipher = sm2.doEncrypt(sm4KeyRaw, sm2PublicKey, 0)
    } catch (e) {
        console.error('gmEncryptPassword sm2.doEncrypt failed:', e)
        // Avoid frontend crashes: return the plaintext on failure, and let the backend handle it as the original value (at least the login/error-viewing flow can continue)
        return password
    }

    const passwordCipher = sm4.encrypt(password, sm4KeyHex)
    // sm2/sm4 output hex by default, but the backend gm.py/session.py needs base64:
    // - sm2_decrypt: base64.b64decode
    // - sm4 decrypt: base64.urlsafe_b64decode
    const keyCipherB64 = bytesToBase64(hexToBytes(keyCipher))
    const passwordCipherB64 = bytesToBase64(hexToBytes(passwordCipher))
    return `${keyCipherB64}:${passwordCipherB64}`
}


function encryptPassword(password) {
    if (!password) {
        console.log('password is empty')
        return ''
    }
    if (typeof password === 'number') {
        password = password.toString()
    }
    let publicKeyText = getCookie('jms_public_key')
    if (!publicKeyText) {
        console.log('publicKeyText is empty')
        return password
    }
    publicKeyText = publicKeyText.replaceAll('"', '')
    publicKeyText = atob(publicKeyText)
    let cipher = ''
    let jmsGMSSL = getCookie('jms_gm_ssl')
    if (publicKeyText.includes('PUBLIC KEY')) {
        jmsGMSSL = '0'
    }
    if (jmsGMSSL === '1') {
        cipher = gmEncryptPassword(password, publicKeyText)
    } else {
        cipher = rsaEncryptPassword(password, publicKeyText)
    }
    
    return cipher
}


function randomString(length) {
    const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = '';
    const charactersLength = characters.length;
    for (let i = 0; i < length; i++) {
        result += characters.charAt(Math.floor(Math.random() * charactersLength));
    }

    return result;
}

function testEncrypt() {
    const radio = []
    const len2 = []
    for (let i = 1; i < 4096; i++) {
        const password = randomString(i)
        const cipher = encryptPassword(password)
        len2.push([password.length, cipher.length])
        radio.push(cipher.length / password.length)
    }
    return radio
}

window.encryptPassword = encryptPassword
