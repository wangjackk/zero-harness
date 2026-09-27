use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChainedErrorWire {
    pub name: String,
    pub msg: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cause: Option<Box<ChainedErrorWire>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChainedError {
    pub name: String,
    pub msg: String,
    pub cause: Option<Box<ChainedError>>,
}

impl ChainedError {
    pub fn leaf(name: impl Into<String>, msg: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            msg: msg.into(),
            cause: None,
        }
    }

    pub fn wrap(name: impl Into<String>, msg: impl Into<String>, cause: ChainedError) -> Self {
        Self {
            name: name.into(),
            msg: msg.into(),
            cause: Some(Box::new(cause)),
        }
    }

    pub fn to_wire(&self) -> ChainedErrorWire {
        ChainedErrorWire {
            name: self.name.clone(),
            msg: self.msg.clone(),
            cause: self.cause.as_ref().map(|cause| Box::new(cause.to_wire())),
        }
    }

    pub fn from_wire(wire: ChainedErrorWire) -> Self {
        Self {
            name: wire.name,
            msg: wire.msg,
            cause: wire.cause.map(|cause| Box::new(Self::from_wire(*cause))),
        }
    }
}
