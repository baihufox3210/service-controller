//! 狀態檔案的讀寫：先寫入暫存檔、fsync，再原子性取代，檔案權限 0600。

use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

pub fn read_optional(path: &Path) -> io::Result<Option<String>> {
    match fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e),
    }
}

/// 原子寫入；若 `backup` 為 true，會先把舊檔複製成 `<檔名>.bak`。
pub fn write_atomic(path: &Path, contents: &str, backup: bool) -> io::Result<()> {
    let dir = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
    fs::create_dir_all(dir)?;

    if backup && path.exists() {
        fs::copy(path, path.with_extension("toml.bak"))?;
    }

    let tmp = dir.join(format!(".{}.tmp", path.file_name().and_then(|n| n.to_str()).unwrap_or("state")));

    let result = (|| {
        let mut file = OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp)?;
        file.write_all(contents.as_bytes())?;
        file.sync_all()?;
        fs::rename(&tmp, path)
    })();

    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }

    result
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::PermissionsExt;

    use super::*;

    #[test]
    fn writes_atomically_with_backup_and_private_permissions() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("services.toml");

        assert_eq!(read_optional(&path).unwrap(), None);

        write_atomic(&path, "a", true).unwrap();
        write_atomic(&path, "b", true).unwrap();

        assert_eq!(fs::read_to_string(&path).unwrap(), "b");
        assert_eq!(fs::read_to_string(dir.path().join("services.toml.bak")).unwrap(), "a");
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);

        let mut names: Vec<_> = fs::read_dir(dir.path()).unwrap().map(|e| e.unwrap().file_name()).collect();
        names.sort();
        assert_eq!(names, ["services.toml", "services.toml.bak"]); // 沒有殘留暫存檔
    }
}
