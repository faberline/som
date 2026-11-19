pub const DEFAULT_LIMIT: usize = 50;
pub const MAX_LIMIT: usize = 200;

/// The cursor is the lower-case hex of `created_at|id`, so clients cannot read
/// or build it by hand.
pub fn encode_cursor(created_at: &str, id: &str) -> String {
    format!("{created_at}|{id}")
        .bytes()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn hex_value(byte: u8) -> Option<u8> {
    (byte as char).to_digit(16).map(|digit| digit as u8)
}

/// `None` for an empty text, an odd length, a non-hex character, bytes that are
/// not UTF-8, a text without `|`, or an empty part on either side of it.
pub fn decode_cursor(text: &str) -> Option<(String, String)> {
    if text.is_empty() || !text.len().is_multiple_of(2) {
        return None;
    }
    let bytes: Vec<u8> = text
        .as_bytes()
        .chunks(2)
        .map(|pair| Some(hex_value(pair[0])? * 16 + hex_value(pair[1])?))
        .collect::<Option<_>>()?;
    let decoded = String::from_utf8(bytes).ok()?;
    let (created_at, id) = decoded.split_once('|')?;
    if created_at.is_empty() || id.is_empty() {
        return None;
    }
    Some((created_at.to_owned(), id.to_owned()))
}
