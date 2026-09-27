use std::path::Path;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut includes = vec!["../../proto"];
    if Path::new("/usr/include/google/protobuf/struct.proto").exists() {
        includes.push("/usr/include");
    }

    tonic_prost_build::configure()
        .build_server(true)
        .build_client(true)
        .compile_protos(&["../../proto/routine.proto"], &includes)?;
    println!("cargo:rerun-if-changed=../../proto/routine.proto");
    Ok(())
}
