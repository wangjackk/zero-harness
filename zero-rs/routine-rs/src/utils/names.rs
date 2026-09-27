pub fn pascal_to_snake(value: &str) -> String {
    let mut out = String::new();
    let mut prev_is_lower_or_digit = false;
    let chars: Vec<char> = value.chars().collect();

    for (idx, ch) in chars.iter().copied().enumerate() {
        let next_is_lower = chars
            .get(idx + 1)
            .map(|next| next.is_ascii_lowercase())
            .unwrap_or(false);
        if ch.is_ascii_uppercase()
            && idx > 0
            && (prev_is_lower_or_digit || next_is_lower)
            && !out.ends_with('_')
        {
            out.push('_');
        }
        out.push(ch.to_ascii_lowercase());
        prev_is_lower_or_digit = ch.is_ascii_lowercase() || ch.is_ascii_digit();
    }

    out
}

#[cfg(test)]
mod tests {
    use super::pascal_to_snake;

    #[test]
    fn converts_pascal_case_like_ts_runtime() {
        assert_eq!(pascal_to_snake("BaseRoutine"), "base_routine");
        assert_eq!(pascal_to_snake("XMLParser"), "xml_parser");
        assert_eq!(pascal_to_snake("GPT4Tool"), "gpt4_tool");
    }
}
